"""Which devices and which channels carry data on Yalgaar right now?

For each logger serial it opens its OWN connection (Yalgaar may allow one channel per connection),
subscribes to <key>/<serial>, and counts the messages per device (headerMac). It prints no keys.

Run (daylight, PC, nothing else connected):   python scripts\\yalgaar_devices.py --seconds 480
"""
from __future__ import annotations
import argparse, collections, datetime as dt, os, sys, threading, time, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import yalgaar_client as yc
from scripts.run_daily import load_env_file

SERIALS = ["1380242000046", "1380244000082", "1380242000050", "1380240700010", "1380244000093",
           "1380242000015", "1380244000091", "1380242000040", "1465242100028"]
KNOWN_MACS = {"0501060806030409": "1380242000046"}      # from the October capture of the test logger


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=480)
    ap.add_argument("--serials", help="comma separated list (default: all 9)")
    ap.add_argument("--host"); ap.add_argument("--port", type=int)
    a = ap.parse_args()
    cfg = yc.config_from_env()
    if cfg is None:
        print("ERROR: YALGAAR_CLIENT_KEY is not set in .env"); return 2
    if a.host: cfg.host = a.host
    if a.port: cfg.port = a.port
    serials = [s.strip() for s in a.serials.split(",")] if a.serials else SERIALS
    yc._allow_yalgaar_suback_codes()
    import paho.mqtt.client as mqtt

    lock = threading.Lock()
    res = {s: {"login": None, "granted": None, "n": 0, "macs": collections.Counter(),
               "newest": {}, "fids": set(), "unread": 0} for s in serials}
    clients = []

    def make(i, serial):
        r = res[serial]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1,
                            client_id=f"{cfg.client_key}/{cfg.uuid}-d{i}", clean_session=True)
        topic = f"{cfg.client_key}/{serial}"
        def on_connect(cl, ud, fl, rc):
            r["login"] = rc
            if rc == 0: cl.subscribe(topic, qos=0)
        def on_subscribe(cl, ud, mid, granted): r["granted"] = list(granted)
        def on_message(cl, ud, msg):
            found = yc.extract_messages(msg.payload.decode("utf-8", errors="replace"))
            with lock:
                if not found: r["unread"] += 1
                for m in found:
                    r["n"] += 1
                    mac = str(m.get("headerMac"))
                    r["macs"][mac] += 1
                    r["fids"].add(m.get("payload", {}).get("functionId"))
                    try:
                        ts = dt.datetime.fromtimestamp(m["timestamp"], dt.timezone.utc).replace(tzinfo=None)
                        if mac not in r["newest"] or ts > r["newest"][mac]: r["newest"][mac] = ts
                    except Exception: pass
        c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message
        try:
            c.connect(cfg.host, cfg.port, keepalive=60); c.loop_start(); clients.append(c)
        except Exception as e:
            r["login"] = f"connect failed: {type(e).__name__}"

    for i, s in enumerate(serials):
        make(i, s); time.sleep(0.4)
    print(f"listening on {len(serials)} channels for {a.seconds}s ...", flush=True)
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(1)
        if int(time.time() - t0) % 60 == 0 and int(time.time() - t0) > 0:
            print(f"  ...{int(time.time() - t0)}s, total messages: {sum(v['n'] for v in res.values())}", flush=True)
    for c in clients:
        c.loop_stop(); c.disconnect()

    now_ist = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)
    print("\n===== DEVICES AND CHANNELS =====")
    all_macs: dict[str, set] = collections.defaultdict(set)
    for s, r in res.items():
        if r["login"] != 0:
            ans = f"login: {yc.explain(r['login'])}" if isinstance(r["login"], int) else f"login: {r['login']}"
        elif r["granted"] is None:
            ans = "subscribe: no answer"
        else:
            ans = ", ".join(yc.explain(x) if x >= 100 else "channel accepted" for x in r["granted"])
        print(f"channel {s}: {ans}   messages: {r['n']}")
        for mac, n in r["macs"].most_common():
            all_macs[mac].add(s)
            age = int((now_ist - r["newest"][mac]).total_seconds() // 60) if mac in r["newest"] else None
            who = f"  (= test logger {KNOWN_MACS[mac]})" if mac in KNOWN_MACS else ""
            print(f"    device {mac}{who}: {n} messages, newest {age} min ago")
        if r["unread"]: print(f"    {r['unread']} message(s) in an unknown layout")
    print(f"\ndistinct devices seen: {len(all_macs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
