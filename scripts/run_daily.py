"""Collect from Yalgaar (or the old username/password broker) -> store -> build the daily HTML report.

Yalgaar path (used when YALGAAR_CLIENT_KEY is set): login is the MQTT client id "<clientKey>/<uuid>",
no username/password. See app/yalgaar_client.py. Each run subscribes to the logger channel, asks
Yalgaar for stored history (max 100 messages) and keeps whatever the broker had queued for us.
Several runs per day (see .github/workflows/daily-report.yml) mean no message is lost even if
history is limited to 100.

Old path (no key set): plain MQTT username/password with a persistent session.

NEVER run two clients with the same client id / uuid at once (your PC and GitHub need different
YALGAAR_UUID values): the broker rejects or kicks one off.
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.html_report import Site, build_daily_html, parse_message, dedupe
from app import yalgaar_client

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"          # one JSONL per IST day, parsed samples only (no credentials)
OUT_DIR = ROOT / "out"


def ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)


def drain(quiet_s: int, max_s: int) -> tuple[list[dict], dict]:
    import paho.mqtt.client as mqtt
    env = lambda k, d="": (os.environ.get(k) or d).strip()
    host, port = env("MQTT_BROKER"), int(env("MQTT_PORT", "1883"))
    serial = env("LOGGER_SERIAL")
    topic = f"solar/{serial}"          # exact topic, built from the serial (MQTT_TOPIC secret is not used)
    cid = env("MQTT_CLIENT_ID") or f"solarpulse_gha_{serial}"
    samples, stats = [], {"msgs": 0, "bad": 0, "connected": False, "subscribed": False}
    last = [time.time()]; done = threading.Event(); lock = threading.Lock()

    kw = dict(client_id=cid, clean_session=False)
    c = (mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, **kw)
         if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client(**kw))
    c.username_pw_set(env("MQTT_USERNAME"), env("MQTT_PASSWORD"))
    if env("MQTT_USE_TLS", "false").lower() in ("1", "true", "yes"):
        c.tls_set()

    def on_connect(cl, ud, flags, rc, *a):
        stats["connected"] = (getattr(rc, "value", rc) == 0)
        stats["connack"] = str(rc)
        print(f"broker answer to login: {rc}  (host={host} port={port} client_id_len={len(cid)} user_len={len(env('MQTT_USERNAME'))})", flush=True)
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

    def on_disconnect(cl, ud, *a):
        print("disconnected:", a, flush=True)

    c.on_connect, c.on_subscribe, c.on_message, c.on_disconnect = on_connect, on_subscribe, on_message, on_disconnect
    try:
        c.connect(host, port, keepalive=60)
    except Exception as e:
        print(f"ERROR: TCP/connect failed: {type(e).__name__}: {e}"); return [], stats
    c.loop_start()
    t0 = time.time()
    while not done.is_set():
        time.sleep(1)
        if time.time() - t0 > max_s: break
        if not stats["connected"] and time.time() - t0 > 60:
            print("ERROR: no login answer from the broker within 60 s"); break
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
    g = lambda k, d=None: (os.environ.get(k) or d or "").strip() or d
    ws = dt.time.fromisoformat(g("WINDOW_START", "07:00")); we = dt.time.fromisoformat(g("WINDOW_END", "18:00"))
    return Site(client=g("CLIENT_NAME", "Client name"), address=g("PLANT_ADDRESS", "—"),
                plant_id=g("PLANT_ID", g("LOGGER_SERIAL", "—")), serial=g("LOGGER_SERIAL", "—"),
                inverter=g("INVERTER", "—"), kwp=float(g("PLANT_KWP")) if g("PLANT_KWP") else None,
                tariff=float(g("TARIFF_INR", "7")), co2_kg_per_kwh=float(g("CO2_KG_PER_KWH", "0.82")),
                window_start=ws, window_end=we)


def load_env_file() -> None:
    """Local PC runs: read .env from the project folder (real environment variables win)."""
    f = ROOT / ".env"
    if not f.is_file(): return
    for line in f.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "/")) or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def collect_yalgaar(cfg, quiet: int, max_s: int) -> int:
    samples, st = yalgaar_client.collect(cfg, quiet, max_s)
    print(f"collect: connected={st['connected']} subscribed={st['subscribed']} live={st['live']} "
          f"history_msgs={st['history_msgs']} samples={len(samples)} unparsed={st['unparsed']} "
          f"login={yalgaar_client.explain(st['connack']) if st['connack'] is not None else 'no answer'}")
    if st["unparsed"]:
        OUT_DIR.mkdir(exist_ok=True)
        (OUT_DIR / "unparsed_sample.txt").write_text("\n\n----\n\n".join(st["unparsed_raw"]), encoding="utf-8")
        print("NOTE: some messages were not in the expected layout. Shapes (no values):")
        for sh in st["unparsed_shapes"]: print("  ", sh)
    if not st["connected"]:
        print("ERROR: could not log in to Yalgaar (see the 'broker answer' line above)"); return 2
    if not st["subscribed"]:
        print("ERROR: logged in, but the channel subscription was refused"); return 2
    store(samples)
    return 0


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="IST day to report (default: today IST)")
    ap.add_argument("--no-collect", action="store_true", help="only rebuild from stored data")
    ap.add_argument("--collect-only", action="store_true", help="collect and store, do not build the report")
    ap.add_argument("--keep-going", action="store_true", help="if collecting fails, still build the report from stored data")
    ap.add_argument("--quiet", type=int, default=45); ap.add_argument("--max", type=int, default=420)
    a = ap.parse_args()
    if not a.no_collect:
        try:
            cfg = yalgaar_client.config_from_env()
        except ValueError as e:
            print("ERROR:", e); return 2
        if cfg is not None:
            rc = collect_yalgaar(cfg, a.quiet, a.max)
            if rc and not a.keep_going: return rc
            if rc: print("WARNING: collecting failed now; building the report from data stored by earlier runs")
        else:
            samples, st = drain(a.quiet, a.max)
            print(f"collect (old username/password path): connected={st['connected']} subscribed={st['subscribed']} "
                  f"messages={st['msgs']} samples={len(samples)} connack={st.get('connack')}")
            if not st["connected"]:
                print("ERROR: could not connect to the broker"); return 2
            store(samples)
    if a.collect_only:
        return 0
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
