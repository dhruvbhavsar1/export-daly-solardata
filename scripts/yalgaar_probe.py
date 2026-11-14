"""Find out WHERE the logger data flows on Yalgaar right now. Prints no keys and no data values.

It logs in with <clientKey>/<uuid> (same as the collector) and listens on a few topics at once:
  A  <key>/<channel>        the Yalgaar channel (what the collector uses)
  B  solar/<serial>         the old raw topic (where October's data came from)
  C  <key>/#                everything under your key (Yalgaar may refuse wildcards)
For every topic it shows the broker's answer, how many messages arrived, and how fresh the newest one is.

Run (daylight, PC, nothing else connected):   python scripts\\yalgaar_probe.py --seconds 420
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys, threading, time, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import yalgaar_client as yc
from scripts.run_daily import load_env_file  # noqa: E402


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=420)
    ap.add_argument("--host", help="override host (tests)"); ap.add_argument("--port", type=int)
    a = ap.parse_args()
    cfg = yc.config_from_env()
    if cfg is None:
        print("ERROR: YALGAAR_CLIENT_KEY is not set in .env"); return 2
    if a.host: cfg.host = a.host
    if a.port: cfg.port = a.port
    serial = (os.environ.get("LOGGER_SERIAL") or cfg.channel).strip()
    topics = {"A  key/channel": cfg.topic, "B  solar/serial": f"solar/{serial}", "C  key/#": f"{cfg.client_key}/#"}
    yc._allow_yalgaar_suback_codes()
    import paho.mqtt.client as mqtt
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cfg.client_id, clean_session=True)
    mids: dict[int, str] = {}
    res = {k: {"granted": None, "n": 0, "newest": None, "topics": set(), "shape": None, "fids": set()} for k in topics}
    login = {}; lock = threading.Lock()

    def on_connect(cl, ud, fl, rc):
        login["rc"] = rc
        print("login:", yc.explain(rc), flush=True)
        if rc == 0:
            for label, t in topics.items():
                _, mid = cl.subscribe(t, qos=0); mids[mid] = label

    def on_subscribe(cl, ud, mid, granted):
        label = mids.get(mid)
        if label: res[label]["granted"] = list(granted)

    def match(label, topic):
        t = topics[label]
        return topic == t or (t.endswith("/#") and topic.startswith(t[:-1]))

    def on_message(cl, ud, msg):
        text = msg.payload.decode("utf-8", errors="replace")
        shown = msg.topic.replace(cfg.client_key, "<key>")
        found = yc.extract_messages(text)
        with lock:
            for label in topics:
                if not match(label, msg.topic): continue
                r = res[label]; r["n"] += 1; r["topics"].add(shown)
                if r["shape"] is None: r["shape"] = yc.describe_shape(text)
                for m in found:
                    r["fids"].add(m.get("payload", {}).get("functionId"))
                    try:
                        ts = dt.datetime.fromtimestamp(m["timestamp"], dt.timezone.utc).replace(tzinfo=None)
                        if r["newest"] is None or ts > r["newest"]: r["newest"] = ts
                    except Exception: pass

    c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message
    try:
        c.connect(cfg.host, cfg.port, keepalive=60)
    except Exception as e:
        print(f"ERROR: connect failed: {type(e).__name__}: {e}"); return 2
    c.loop_start()
    t0 = time.time()
    while time.time() - t0 < a.seconds and login.get("rc", 0) == 0:
        time.sleep(1)
        if int(time.time() - t0) % 60 == 0:
            print(f"  ...{int(time.time() - t0)}s, messages so far: " + ", ".join(f"{k[0]}={v['n']}" for k, v in res.items()), flush=True)
    c.loop_stop(); c.disconnect()

    now_ist = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)
    print("\n===== PROBE RESULT =====")
    for label, r in res.items():
        g = r["granted"]
        ans = "no answer" if g is None else ", ".join(yc.explain(x) if x >= 100 else f"granted QoS {x}" for x in g)
        age = "-" if r["newest"] is None else f"newest reading {r['newest']:%H:%M} (device clock), {int((now_ist - r['newest']).total_seconds() // 60)} min ago"
        print(f"{label:16} answer: {ans}")
        print(f"{'':16} messages: {r['n']}   {age}   functionIds: {sorted(x for x in r['fids'] if x is not None)}")
        if r["topics"]: print(f"{'':16} topics seen: {sorted(r['topics'])[:4]}")
        if r["shape"]: print(f"{'':16} layout: {r['shape']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
