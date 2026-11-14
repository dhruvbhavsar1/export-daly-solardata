"""Send ONE test message to a separate test channel, to check that the Yalgaar webhook fires.

The message goes to the channel "webhooktest" (not your logger's channel), so no logger data is touched.
If your webhook.site page then shows a POST request, the webhook works.

Run:   python scripts\\yalgaar_webhook_test.py
"""
from __future__ import annotations
import datetime as dt, json, sys, threading, time, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import yalgaar_client as yc
from scripts.run_daily import load_env_file


def main() -> int:
    load_env_file()
    import os
    cfg = yc.config_from_env()
    if cfg is None:
        print("ERROR: YALGAAR_CLIENT_KEY is not set in .env"); return 2
    if os.environ.get("TEST_HOST"): cfg.host = os.environ["TEST_HOST"]
    if os.environ.get("TEST_PORT"): cfg.port = int(os.environ["TEST_PORT"])
    import paho.mqtt.client as mqtt
    yc._allow_yalgaar_suback_codes()
    cid = f"{cfg.client_key}/{cfg.uuid}-wt"          # separate uuid, never clashes with the collector
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cid, clean_session=True)
    res = {}
    c.on_connect = lambda cl, ud, fl, rc: res.setdefault("rc", rc)
    c.on_disconnect = lambda cl, ud, rc: res.setdefault("disc", rc)
    try:
        c.connect(cfg.host, cfg.port, keepalive=30)
    except Exception as e:
        print(f"ERROR: connect failed: {type(e).__name__}: {e}"); return 2
    c.loop_start(); t0 = time.time()
    while "rc" not in res and time.time() - t0 < 15: time.sleep(0.2)
    print("login:", yc.explain(res.get("rc")) if "rc" in res else "no answer")
    if res.get("rc") != 0:
        c.loop_stop(); return 2
    topic = f"{cfg.client_key}/webhooktest"
    body = json.dumps({"hello": "solarpulse webhook test", "sent_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    info = c.publish(topic, body, qos=0)
    print("publish call result code:", info.rc, "(0 = handed to the network)")
    time.sleep(6)
    if "disc" in res: print("note: the broker closed the connection (paho code", res["disc"], ") - publish may be refused for this uuid")
    c.loop_stop(); c.disconnect()
    print("done. Now look at webhook.site for a POST request (can take up to a minute).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
