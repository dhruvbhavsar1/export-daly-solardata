"""Nightly job: drain the broker's queued messages -> store -> build the daily HTML report.

Why this works without a 24/7 server: the broker keeps QoS-1 messages for a persistent
session (clean_session=False + a fixed client id). Each run reconnects with the SAME id and
receives everything sent since the last run. (Seen in your capture: one connect returned 1070
queued messages with device times up to 8 days old.)

NEVER run two clients with the same MQTT_CLIENT_ID at once (e.g. your PC and GitHub):
the broker kicks one off and the queue is split. Give each its own id, or stop one.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.html_report import Site, build_daily_html, parse_message, dedupe

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"          # one JSONL per IST day, parsed samples only (no credentials)
OUT_DIR = ROOT / "out"


def ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)


def drain(quiet_s: int, max_s: int) -> tuple[list[dict], dict]:
    import paho.mqtt.client as mqtt
    host, port = os.environ["MQTT_BROKER"], int(os.environ["MQTT_PORT"])
    serial = os.environ["LOGGER_SERIAL"]
    topic = os.environ.get("MQTT_TOPIC", f"solar/{serial}")
    cid = os.environ.get("MQTT_CLIENT_ID") or f"solarpulse_gha_{serial}"
    samples, stats = [], {"msgs": 0, "bad": 0, "connected": False, "subscribed": False}
    last = [time.time()]; done = threading.Event(); lock = threading.Lock()

    kw = dict(client_id=cid, clean_session=False)
    c = (mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, **kw)
         if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client(**kw))
    c.username_pw_set(os.environ["MQTT_USERNAME"], os.environ["MQTT_PASSWORD"])
    if os.environ.get("MQTT_USE_TLS", "false").lower() in ("1", "true", "yes"):
        c.tls_set()

    def on_connect(cl, ud, flags, rc, *a):
        stats["connected"] = (getattr(rc, "value", rc) == 0)
        if stats["connected"]:
            cl.subscribe(topic, qos=1)
        else:
            done.set()

    def on_subscribe(cl, ud, mid, *a):
        stats["subscribed"] = True; last[0] = time.time()

    def on_message(cl, ud, msg):
        with lock:
            stats["msgs"] += 1; last[0] = time.time()
            try:
                s = parse_message(json.loads(msg.payload))
            except Exception:
                s = None
            if s: samples.append(s)
            elif s is None: stats["bad"] += 0  # non-telemetry (e.g. fault flags) is ignored on purpose

    c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message
    c.connect(host, port, keepalive=60); c.loop_start()
    t0 = time.time()
    while not done.is_set():
        time.sleep(1)
        if time.time() - t0 > max_s: break
        if stats["subscribed"] and time.time() - last[0] > quiet_s: break   # queue drained
    c.loop_stop(); c.disconnect()           # persistent session stays on the broker
    return samples, stats


def store(samples: list[dict]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    by_day: dict[dt.date, list[dict]] = {}
    for s in samples: by_day.setdefault(s["t"].date(), []).append(s)
    for day, new in by_day.items():
        f = RAW_DIR / f"{day}.jsonl"
        old = [dict(json.loads(l), t=dt.datetime.fromisoformat(json.loads(l)["t"])) for l in f.read_text().splitlines()] if f.exists() else []
        merged = dedupe(old + new)
        f.write_text("\n".join(json.dumps({**m, "t": m["t"].isoformat()}) for m in merged) + "\n")


def load_day(day: dt.date) -> list[dict]:
    f = RAW_DIR / f"{day}.jsonl"
    if not f.exists(): return []
    return [dict(json.loads(l), t=dt.datetime.fromisoformat(json.loads(l)["t"])) for l in f.read_text().splitlines()]


def site_from_env() -> Site:
    g = os.environ.get
    ws = dt.time.fromisoformat(g("WINDOW_START", "07:00")); we = dt.time.fromisoformat(g("WINDOW_END", "18:00"))
    return Site(client=g("CLIENT_NAME", "Client name"), address=g("PLANT_ADDRESS", "—"),
                plant_id=g("PLANT_ID", g("LOGGER_SERIAL", "—")), serial=g("LOGGER_SERIAL", "—"),
                inverter=g("INVERTER", "—"), kwp=float(g("PLANT_KWP")) if g("PLANT_KWP") else None,
                tariff=float(g("TARIFF_INR", "7")), co2_kg_per_kwh=float(g("CO2_KG_PER_KWH", "0.82")),
                window_start=ws, window_end=we)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="IST day to report (default: today IST)")
    ap.add_argument("--no-collect", action="store_true", help="only rebuild from stored data")
    ap.add_argument("--quiet", type=int, default=45); ap.add_argument("--max", type=int, default=420)
    a = ap.parse_args()
    if not a.no_collect:
        samples, st = drain(a.quiet, a.max)
        print(f"collect: connected={st['connected']} subscribed={st['subscribed']} messages={st['msgs']} samples={len(samples)}")
        if not st["connected"]:
            print("ERROR: could not connect to the broker"); return 2
        store(samples)
    day = dt.date.fromisoformat(a.date) if a.date else ist_now().date()
    cur = load_day(day)
    prev_e = max((x["e_kwh"] for x in load_day(day - dt.timedelta(days=1))), default=None)
    month = sum(max(x["e_kwh"] for x in load_day(d)) for d in (day.replace(day=1) + dt.timedelta(days=i) for i in range(day.day)) if load_day(d))
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / f"solar_report_{day}.html"
    out.write_text(build_daily_html(cur, day, site_from_env(), prev_e, month or None), encoding="utf-8")
    print("report:", out, "| readings today:", len(dedupe(cur)))
    return 0 if cur else 3


if __name__ == "__main__":
    sys.exit(main())
