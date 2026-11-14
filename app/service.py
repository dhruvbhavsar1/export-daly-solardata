"""SolarPulse service: collect from Yalgaar all day, store, and finalize the day (summary + report + email).

* One Yalgaar connection per channel (<key>/<channel>) with its OWN persistent session. Your devices all publish on ONE
  channel (1380242000046); the message has no serial, only the device id (headerMac).
* Every reading is stored by device id. loggers.json maps device ids to customer loggers ("macs").
* Window 07:00-18:00 IST (setting). After the window: daily summary (kept forever), HTML + PDF report, email.
* Raw readings are pruned after RAW_KEEP_DAYS (default 90).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app import yalgaar_client as yc
from app.html_report import Site, build_daily_html, dedupe, parse_faults, parse_message, summarize
from app.trips import detect_trips
from app.weather import get_weather
from app.mailer import send as send_mail, split_addresses
from app.store import Store

log = logging.getLogger("solarpulse")
ROOT = Path(__file__).resolve().parents[1]


def ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)


# ------------------------------------------------------------------ configuration
@dataclass
class LoggerCfg:
    serial: str
    name: str = ""
    address: str = "-"
    inverter: str = "-"
    kwp: Optional[float] = None
    macs: list[str] = field(default_factory=list)      # device id(s) (headerMac) that belong to this logger
    emails: list[str] = field(default_factory=list)
    plant: dict = field(default_factory=dict)          # customer / plant details from plants.json


def load_loggers(path: Path, only: Optional[list[str]] = None, plants_path: Optional[Path] = None) -> list[LoggerCfg]:
    items = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    out = [LoggerCfg(serial=str(i["serial"]).strip(), name=i.get("name", ""), address=i.get("address", "-"),
                     inverter=i.get("inverter", "-"), kwp=i.get("kwp"),
                     macs=[str(m).strip() for m in ([i["mac"]] if i.get("mac") else []) + list(i.get("macs", [])) if str(m).strip()],
                     emails=split_addresses(",".join(i.get("emails", [])))) for i in items]
    pp = Path(plants_path) if plants_path else Path(path).with_name("plants.json")
    plants = {str(p["serial"]).strip(): p for p in json.loads(pp.read_text(encoding="utf-8-sig"))} if pp.is_file() else {}
    for lg in out:                                      # plants.json = who the customer is; loggers.json = devices and e-mails
        p = plants.get(lg.serial)
        if p:
            lg.plant = p
            lg.name, lg.address = p.get("client") or lg.name, p.get("address") or lg.address
            lg.kwp = p.get("kwp", lg.kwp)
            if p.get("mac") and str(p["mac"]) not in lg.macs:
                lg.macs.append(str(p["mac"]))
    return [x for x in out if not only or x.serial in only]


@dataclass
class Settings:
    window_start: dt.time = dt.time(7, 0)
    window_end: dt.time = dt.time(18, 0)
    keep_days: int = 90
    tariff: float = 7.0
    co2: float = 0.82
    psh: float = 4.5                    # assumed peak sun hours per day (PSH_HOURS_PER_DAY); used for performance ratio
    send_to_clients: bool = False
    owner_mail: list[str] = field(default_factory=list)
    out_dir: Path = ROOT / "out"
    make_pdf: bool = True
    send_email: bool = True

    @staticmethod
    def from_env(env=None) -> "Settings":
        g = (env or os.environ).get
        v = lambda k, d: (g(k) or d).strip()
        return Settings(
            window_start=dt.time.fromisoformat(v("WINDOW_START", "07:00")),
            window_end=dt.time.fromisoformat(v("WINDOW_END", "18:00")),
            keep_days=int(v("RAW_KEEP_DAYS", "90")), tariff=float(v("TARIFF_INR", "7")),
            co2=float(v("CO2_KG_PER_KWH", "0.82")), psh=float(v("PSH_HOURS_PER_DAY", "4.5")),
            send_to_clients=v("SEND_TO_CLIENTS", "false").lower() in ("1", "true", "yes"),
            owner_mail=split_addresses(g("MAIL_TO")))


def site_for(lg: LoggerCfg, st: Settings) -> Site:
    p = lg.plant or {}
    num = lambda k: float(p[k]) if p.get(k) not in (None, "") else None
    comm = dt.date.fromisoformat(p["commissioned"]) if p.get("commissioned") else None
    return Site(client=lg.name or lg.serial, address=lg.address, plant_id=p.get("plant_id") or lg.serial, serial=lg.serial,
                inverter=lg.inverter, kwp=lg.kwp, tariff=num("tariff_inr") or st.tariff, co2_kg_per_kwh=st.co2,
                window_start=st.window_start, window_end=st.window_end, psh=num("psh") or st.psh,
                inverter_make=p.get("inverter_make") or "", inverter_model=p.get("inverter_model") or "",
                inverter_sn=p.get("inverter_sn") or "", panel_type=p.get("panel_type") or "",
                plan=p.get("plan") or "Premium", invested_inr=num("invested_inr"), commissioned=comm,
                city=p.get("city") or "Surat", lat=num("lat") or 21.17, lon=num("lon") or 72.83)


# ------------------------------------------------------------------ collecting
class ChannelWorker(threading.Thread):
    """One Yalgaar channel = one connection. Reconnects by itself (backoff 5 s -> 2 min)."""
    BACKOFF_START, BACKOFF_MAX, HEALTHY_AFTER = 5, 120, 90

    def __init__(self, cfg: yc.YalgaarConfig, channel: str, out: "queue.Queue", stop: threading.Event):
        super().__init__(daemon=True, name=f"ch-{channel}")
        self.cfg, self.serial, self.out, self.stop_ev = cfg, channel, out, stop   # self.serial = channel name
        self.stats = {"connects": 0, "messages": 0, "last_msg": None, "last_error": None, "up": False}

    def run(self) -> None:
        import paho.mqtt.client as mqtt
        yc._allow_yalgaar_suback_codes()
        backoff = self.BACKOFF_START
        while not self.stop_ev.is_set():
            lost = threading.Event()
            t0 = time.time()
            uuid = f"{self.cfg.uuid}-{self.serial}"[:50]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=f"{self.cfg.client_key}/{uuid}",
                                clean_session=not self.cfg.persistent)
            if self.cfg.secure:
                c.tls_set()
            topic = f"{self.cfg.client_key}/{self.serial}"

            def on_connect(cl, ud, fl, rc, _l=lost):
                if rc != 0:
                    self.stats["last_error"] = yc.explain(rc)
                    log.warning("%s: login refused: %s", self.serial, yc.explain(rc)); _l.set(); return
                self.stats["connects"] += 1
                cl.subscribe(topic, qos=1 if self.cfg.persistent else 0)

            def on_subscribe(cl, ud, mid, granted, _l=lost):
                bad = [x for x in granted if x >= 100]
                if bad:
                    self.stats["last_error"] = "; ".join(yc.explain(x) for x in bad)
                    log.warning("%s: subscribe refused: %s", self.serial, self.stats["last_error"]); _l.set()
                else:
                    self.stats["up"] = True; self.stats["last_error"] = None
                    log.info("%s: listening on its channel", self.serial)

            def on_message(cl, ud, msg):
                text = msg.payload.decode("utf-8", errors="replace")
                for m in yc.extract_messages(text):
                    s = parse_message(m)
                    if s:
                        self.stats["messages"] += 1; self.stats["last_msg"] = time.time()
                        self.out.put((self.serial, s))
                    else:
                        fa = parse_faults(m)                 # fault flags (functionId 15) are stored for trip detection
                        if fa:
                            self.out.put(("__fault__", fa))

            def on_disconnect(cl, ud, rc, _l=lost):
                self.stats["up"] = False; _l.set()

            c.on_connect, c.on_subscribe, c.on_message, c.on_disconnect = on_connect, on_subscribe, on_message, on_disconnect
            try:
                c.connect(self.cfg.host, self.cfg.port, keepalive=60)
                c.loop_start()
                while not self.stop_ev.is_set() and not lost.is_set():
                    self.stop_ev.wait(1)
            except Exception as e:
                self.stats["last_error"] = f"{type(e).__name__}: {e}"
                log.warning("%s: connection problem: %s", self.serial, self.stats["last_error"])
            finally:
                self.stats["up"] = False
                try:
                    c.loop_stop(); c.disconnect()
                except Exception:
                    pass
            if self.stop_ev.is_set():
                break
            backoff = self.BACKOFF_START if time.time() - t0 > self.HEALTHY_AFTER else min(backoff * 2, self.BACKOFF_MAX)
            if "107" in str(self.stats["last_error"]):       # connection limit: be gentle
                backoff = max(backoff, 60)
            log.info("%s: reconnecting in %ss", self.serial, backoff)
            self.stop_ev.wait(backoff)


class Writer(threading.Thread):
    """Single writer: takes readings from the queue and stores them in small batches."""

    def __init__(self, store: Store, q: "queue.Queue", stop: threading.Event):
        super().__init__(daemon=True, name="writer")
        self.store, self.q, self.stop_ev, self.stored = store, q, stop, 0

    def _flush(self, batch: dict) -> None:
        for serial, rows in batch.items():
            try:
                if serial == "__fault__":
                    for fa in rows:
                        self.store.add_fault(fa)
                    continue
                self.stored += self.store.add_samples(serial, rows)
            except Exception:
                log.exception("could not store readings for %s", serial)

    def run(self) -> None:
        batch: dict[str, list] = {}
        last = time.time()
        while not (self.stop_ev.is_set() and self.q.empty()):
            try:
                serial, s = self.q.get(timeout=1)
                batch.setdefault(serial, []).append(s)
            except queue.Empty:
                pass
            if batch and time.time() - last >= 3:
                self._flush(batch); batch = {}; last = time.time()
        self._flush(batch)


def collect_window(cfg: yc.YalgaarConfig, channels: list[str], store: Store, until: dt.datetime,
                   clock: Callable[[], dt.datetime] = ist_now, heartbeat_s: int = 600) -> None:
    stop, q = threading.Event(), queue.Queue()
    writer = Writer(store, q, stop)
    workers = [ChannelWorker(cfg, ch, q, stop) for ch in channels]
    writer.start()
    for i, w in enumerate(workers):
        w.start(); time.sleep(0.3)
    log.info("collecting from %d Yalgaar channel(s) until %s IST", len(workers), until.strftime("%H:%M"))
    last_hb = time.time()
    try:
        while clock() < until:
            time.sleep(1)
            if time.time() - last_hb >= heartbeat_s:
                last_hb = time.time()
                up = sum(1 for w in workers if w.stats["up"])
                log.info("heartbeat: %d/%d channels up, %d new readings stored today so far", up, len(workers), writer.stored)
                for w in workers:
                    if not w.stats["up"]:
                        log.warning("  %s is down: %s", w.serial, w.stats["last_error"])
    finally:
        stop.set()
        for w in workers:
            w.join(timeout=10)
        writer.join(timeout=15)
    log.info("collection finished: %d new readings stored", writer.stored)


# ------------------------------------------------------------------ finalizing
def _pdf(html_path: Path) -> Optional[Path]:
    pdf = html_path.with_suffix(".pdf")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "html_to_pdf.py"), str(html_path), str(pdf)],
                       check=True, timeout=180, capture_output=True)
        return pdf
    except Exception as e:
        log.warning("PDF not created (%s). Install playwright + chromium to get PDFs.", type(e).__name__)
        return None


def finalize_day(day: dt.date, loggers: list[LoggerCfg], store: Store, st: Settings) -> list[dict]:
    st.out_dir.mkdir(parents=True, exist_ok=True)
    expected = int((dt.datetime.combine(day, st.window_end) - dt.datetime.combine(day, st.window_start)).total_seconds() / 300)
    seen = store.macs_for_day(day)
    assigned = {m for lg in loggers for m in lg.macs}
    unassigned = {m: n for m, n in seen.items() if m not in assigned}
    if unassigned:
        log.warning("%d device id(s) are not assigned to any logger: %s  (use scripts\\devices.py)", len(unassigned), unassigned)
    rows: list[dict] = []
    for lg in loggers:
        row = {"serial": lg.serial, "name": lg.name or lg.serial, "status": "NO DATA", "energy": None, "peak": None, "n": 0}
        if not lg.macs:
            row["status"] = "NO DEVICE ID SET"
            log.warning("%s: no device id assigned in loggers.json", lg.serial); rows.append(row); continue
        counts = {m: store.count_for_mac(m, day) for m in lg.macs}
        mac = max(counts, key=counts.get)
        if counts[mac] == 0:
            log.warning("%s: no readings on %s", lg.serial, day); rows.append(row); continue
        samples = store.day_samples(mac, day)
        site = site_for(lg, st)
        m = summarize(samples, site)
        if not m.get("n"):
            rows.append(row); continue
        faults = store.day_faults(mac, day)
        trips = detect_trips(samples, faults, day, site.tariff)
        html = build_daily_html(samples, day, site, history=store.energy_history(lg.serial), faults=faults or None,
                                trips=trips, month_trips=store.trips_before(lg.serial, day),
                                weather=get_weather(site.lat, site.lon, day))
        hp = st.out_dir / f"solar_report_{lg.serial}_{day}.html"
        hp.write_text(html, encoding="utf-8")
        pdf = _pdf(hp) if st.make_pdf else None
        store.upsert_summary(lg.serial, day, mac, m, expected)
        store.replace_trips(lg.serial, day, trips)
        row.update(status="OK", energy=m["energy"], peak=m["peak_kw"], n=m["n"], expected=expected)
        rows.append(row)
        if st.send_email:
            to = lg.emails if (st.send_to_clients and lg.emails) else st.owner_mail
            try:
                headline = f"{m['energy']:.1f} kWh · peak {m['peak_kw']:.2f} kW"
                send_mail(f"Solar report {day} · {lg.name or lg.serial} · {headline}",
                          f"Solar daily report for {lg.name or lg.serial}, {day}\n{headline}\nOpen the attached HTML in a browser for the interactive chart.",
                          _mail_html(lg.name or lg.serial, day, m), to, [hp] + ([pdf] if pdf else []))
                log.info("%s: report emailed to %s", lg.serial, ", ".join(to))
            except Exception:
                log.exception("%s: email failed", lg.serial)
    if st.send_email and st.owner_mail:
        try:
            _digest(day, rows, st, unassigned)
        except Exception:
            log.exception("digest email failed")
    pruned = store.prune(st.keep_days, day)
    if pruned:
        log.info("pruned %d raw readings older than %d days", pruned, st.keep_days)
    store.mark_finalized(day)
    return rows


def _mail_html(name: str, day: dt.date, m: dict) -> str:
    return f"""<div style="font-family:Segoe UI,Arial,sans-serif;max-width:560px;margin:auto;background:#F5F7F3;padding:24px;color:#18372F">
<div style="font-size:22px;font-weight:800"><span style="color:#0B2A6B">SOLSERVA</span> <span style="color:#1B6B1E;font-size:15px">GREENTECH PVT LTD</span></div>
<div style="color:#61776E;font-size:12px;letter-spacing:1.5px">DAILY REPORT · {day}</div>
<div style="background:#fff;border:1px solid #DCE7DF;border-radius:14px;padding:20px;margin-top:16px">
<div style="color:#61776E;font-size:13px">{name}</div>
<div style="font-size:30px;font-weight:800;color:#BA8000;margin:6px 0">{m['energy']:.1f} kWh</div>
<div style="font-size:14px">Peak {m['peak_kw']:.2f} kW · first light {m['first']:%H:%M} · last light {m['last']:%H:%M}</div></div>
<p style="font-size:13px;color:#61776E">Open the attached <b>HTML</b> file in your browser for the interactive power curve. The <b>PDF</b> is a printable copy.</p></div>"""


def _digest(day: dt.date, rows: list[dict], st: Settings, unassigned: Optional[dict] = None) -> None:
    def num(x, nd):
        return "-" if x is None else format(x, "." + str(nd) + "f")
    total = sum(r["energy"] or 0 for r in rows)
    ok = sum(1 for r in rows if r["status"] == "OK")
    body = ""
    for r in rows:
        body += ("<tr><td>%s</td><td>%s</td><td style='text-align:right'>%s</td><td style='text-align:right'>%s</td>"
                 "<td style='text-align:right'>%s</td><td>%s</td></tr>") % (
            r["name"], r["serial"], num(r["energy"], 1), num(r["peak"], 2), r["n"], r["status"])
    extra = ""
    if unassigned:
        extra = "<p><b>Devices not assigned to any logger yet:</b> " + ", ".join("%s (%d readings)" % (m, n) for m, n in sorted(unassigned.items())) + "</p>"
    html = ("<div style='font-family:Segoe UI,Arial,sans-serif'><h3>Solar summary %s</h3>"
            "<table cellpadding='6' style='border-collapse:collapse;font-size:13px' border='1'>"
            "<tr><th>Logger</th><th>Serial</th><th>kWh</th><th>Peak kW</th><th>Readings</th><th>Status</th></tr>%s</table>"
            "<p>Total: <b>%.1f kWh</b>. Loggers without data: %d.</p>%s</div>") % (day, body, total, len(rows) - ok, extra)
    txt = "\n".join("%s %s: %s %s kWh" % (r["serial"], r["name"], r["status"], num(r["energy"], 1)) for r in rows)
    send_mail("Solar summary %s | %.1f kWh | %d/%d loggers" % (day, total, ok, len(rows)), txt, html, st.owner_mail)


# ------------------------------------------------------------------ main loop
def run_forever(cfg: yc.YalgaarConfig, channels: list[str], loggers: list[LoggerCfg], store: Store, st: Settings,
                clock: Callable[[], dt.datetime] = ist_now, stop: Optional[threading.Event] = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        now = clock()
        start, end = dt.datetime.combine(now.date(), st.window_start), dt.datetime.combine(now.date(), st.window_end)
        if now < start:
            log.info("waiting until %s IST", start.strftime("%H:%M"))
            while clock() < start and not stop.is_set():
                stop.wait(20)
            continue
        if now < end:
            collect_window(cfg, channels, store, end, clock)
            now = clock()
        if not store.is_finalized(now.date()):
            log.info("finalizing %s", now.date())
            try:
                finalize_day(now.date(), loggers, store, st)
            except Exception:
                log.exception("finalize failed; will retry in 10 minutes")
                stop.wait(600); continue
        tomorrow = dt.datetime.combine(now.date() + dt.timedelta(days=1), st.window_start)
        log.info("done for today; sleeping until %s", tomorrow)
        while clock() < tomorrow and not stop.is_set():
            stop.wait(30)