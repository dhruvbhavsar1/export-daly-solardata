"""Old-style login test, now with Yalgaar's real error numbers. Prints no keys and no passwords.

Tries the way October's data was read (username/password + topic solar/<serial>) on several hosts and
client-id styles, and shows the broker's answer for each. If one logs in AND subscribes, it listens
for --listen seconds and counts messages.

Run (daylight, nothing else connected):   python scripts\\yalgaar_raw_test.py --listen 120
"""
from __future__ import annotations
import argparse, os, sys, threading, time, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import yalgaar_client as yc
from scripts.run_daily import load_env_file


def attempt(host, port, cid, user, pw, topic, listen):
    import paho.mqtt.client as mqtt
    r = {"login": None, "sub": None, "n": 0, "err": None}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, clean_session=True)
    if user: c.username_pw_set(user, pw or "")
    def on_connect(cl, ud, fl, rc):
        r["login"] = rc
        if rc == 0: cl.subscribe(topic, qos=0)
    def on_subscribe(cl, ud, mid, granted): r["sub"] = list(granted)
    def on_message(cl, ud, msg): r["n"] += 1
    c.on_connect, c.on_subscribe, c.on_message = on_connect, on_subscribe, on_message
    try:
        c.connect(host, port, keepalive=30)
    except Exception as e:
        r["err"] = f"{type(e).__name__}: {e}"; return r
    c.loop_start(); t0 = time.time()
    while r["login"] is None and time.time() - t0 < 12: time.sleep(0.2)
    if r["login"] == 0:
        while r["sub"] is None and time.time() - t0 < 20: time.sleep(0.2)
        if r["sub"] and all(x < 100 for x in r["sub"]):
            time.sleep(listen)
    c.loop_stop(); c.disconnect()
    return r


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", type=int, default=120)
    ap.add_argument("--only", help="test one host:port only (tests)")
    a = ap.parse_args()
    g = lambda k: (os.environ.get(k) or "").strip()
    serial, user, pw = g("LOGGER_SERIAL"), g("MQTT_USERNAME"), g("MQTT_PASSWORD")
    key, uuid = g("YALGAAR_CLIENT_KEY"), g("YALGAAR_UUID") or "solarpulse-pc"
    if not serial: print("ERROR: LOGGER_SERIAL missing in .env"); return 2
    yc._allow_yalgaar_suback_codes()
    hosts = [(a.only.split(":")[0], int(a.only.split(":")[1]))] if a.only else \
            [("api.yalgaar.io", 1883), ("3.7.240.91", 8883), ("3.7.240.91", 1883)]
    plan = []
    for h, p in hosts:
        plan.append((h, p, f"solar_raw_{serial}", user, pw, "old style: client id solar_raw_<serial> + username/password"))
        if key and h == "api.yalgaar.io" or a.only:
            plan.append((h, p, f"{key}/{uuid}", user, pw, "mixed: client id <key>/<uuid> + username/password"))
    print(f"password set: {bool(pw)} (length {len(pw)}), username: {user or '(none)'}")
    for h, p, cid, u, w, label in plan:
        topic = f"solar/{serial}"
        print(f"\n[{h}:{p}] {label}", flush=True)
        r = attempt(h, p, cid, u, w, topic, a.listen)
        if r["err"]:
            print("   could not connect:", r["err"]); continue
        print("   login    :", "no answer" if r["login"] is None else yc.explain(r["login"]))
        if r["login"] == 0:
            print("   subscribe:", "no answer" if r["sub"] is None else ", ".join(yc.explain(x) if x >= 100 else f"granted QoS {x}" for x in r["sub"]))
            print("   messages :", r["n"], f"in {a.listen}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
