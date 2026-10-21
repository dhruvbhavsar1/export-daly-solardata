"""
Diagnostic collector — adds verbose logging at every decision point.
Runs for a configurable duration and reports exactly what Paho's callbacks receive.
Does NOT log credentials or full payloads.
Run with:  python -m app.diag_collector --duration 120
"""

from __future__ import annotations

import argparse
import datetime
import sys
import threading
import time
import uuid

import paho.mqtt.client as mqtt

from app.config import ConfigError, load_config


def ts() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def run_diagnostic(duration: int) -> None:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"[FATAL] Config error: {e}")
        sys.exit(1)

    # ── Reproduce EXACTLY the new collector's session settings ──────────────
    client_id = f"solar-collector-{config.logger_serial}"   # stable, persistent
    clean_session = False

    print(f"[{ts()}] [DIAG] client_id      : {client_id!r}")
    print(f"[{ts()}] [DIAG] clean_session  : {clean_session}")
    print(f"[{ts()}] [DIAG] topic          : {config.topic!r}")
    print(f"[{ts()}] [DIAG] broker         : {config.broker}:{config.port}")
    print(f"[{ts()}] [DIAG] duration       : {duration}s")
    print(f"[{ts()}] [DIAG] paho version   : {getattr(mqtt, '__version__', 'n/a')}")

    # ── Create client (VERSION2 callback API, same as app/collector.py) ──────
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=clean_session,
    )
    client.username_pw_set(config.username, config.password)

    # ── Activate Paho's own internal logger so we can see its decisions ──────
    import logging
    logging.basicConfig(level=logging.DEBUG)
    client.enable_logger(logging.getLogger("paho"))

    connected_event = threading.Event()
    subscribed_event = threading.Event()
    callback_message_count = 0
    buffer_message_count = 0

    # ── on_connect ───────────────────────────────────────────────────────────
    def on_connect(c, userdata, flags, reason_code, properties=None):
        print(f"\n[{ts()}] [DIAG][on_connect] fired")
        print(f"[{ts()}] [DIAG][on_connect] flags         : {flags!r}  (type={type(flags).__name__})")
        print(f"[{ts()}] [DIAG][on_connect] reason_code   : {reason_code!r}")
        print(f"[{ts()}] [DIAG][on_connect] is_failure    : {getattr(reason_code, 'is_failure', 'N/A')}")

        is_failure = getattr(reason_code, "is_failure", None)
        if is_failure is None:
            is_failure = (reason_code != 0)

        if not is_failure:
            print(f"[{ts()}] [DIAG][on_connect] CONNECTION SUCCESS — subscribing to {config.topic!r} QoS=1")
            res, mid = c.subscribe(config.topic, qos=1)
            print(f"[{ts()}] [DIAG][on_connect] subscribe() returned res={res}, mid={mid}")
            if res != mqtt.MQTT_ERR_SUCCESS:
                print(f"[{ts()}] [DIAG][on_connect] WARNING: subscribe() returned non-success code {res}")
        else:
            print(f"[{ts()}] [DIAG][on_connect] CONNECTION REFUSED: {reason_code}")

        connected_event.set()

    # ── on_subscribe ─────────────────────────────────────────────────────────
    def on_subscribe(c, userdata, mid, reason_codes, properties=None):
        print(f"\n[{ts()}] [DIAG][on_subscribe] fired")
        print(f"[{ts()}] [DIAG][on_subscribe] mid          : {mid}")
        print(f"[{ts()}] [DIAG][on_subscribe] reason_codes : {reason_codes!r}")
        first = reason_codes[0] if isinstance(reason_codes, list) and reason_codes else reason_codes
        is_fail = getattr(first, "is_failure", None)
        if is_fail is None:
            is_fail = (first >= 128) if isinstance(first, int) else False
        print(f"[{ts()}] [DIAG][on_subscribe] is_failure   : {is_fail}")
        subscribed_event.set()

    # ── on_message ───────────────────────────────────────────────────────────
    def on_message(c, userdata, message):
        nonlocal callback_message_count, buffer_message_count
        callback_message_count += 1
        print(f"\n[{ts()}] [DIAG][on_message] CALLBACK FIRED #{callback_message_count}")
        print(f"[{ts()}] [DIAG][on_message] topic        : {message.topic!r}")
        print(f"[{ts()}] [DIAG][on_message] payload_len  : {len(message.payload)} bytes")
        print(f"[{ts()}] [DIAG][on_message] qos          : {message.qos}")
        print(f"[{ts()}] [DIAG][on_message] retain       : {message.retain}")
        # Append to buffer
        try:
            buffer_message_count += 1
            print(f"[{ts()}] [DIAG][on_message] buffer count now: {buffer_message_count}")
        except Exception as exc:
            print(f"[{ts()}] [DIAG][on_message] EXCEPTION IN BUFFER APPEND: {exc}")

    # ── on_disconnect ─────────────────────────────────────────────────────────
    def on_disconnect(c, userdata, flags_or_rc, reason_code=None, properties=None):
        rc = reason_code if reason_code is not None else flags_or_rc
        print(f"\n[{ts()}] [DIAG][on_disconnect] fired  rc={rc!r}")

    client.on_connect = on_connect
    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    print(f"\n[{ts()}] [DIAG] Registered callbacks:")
    print(f"  on_connect   : {client.on_connect}")
    print(f"  on_subscribe : {client.on_subscribe}")
    print(f"  on_message   : {client.on_message}")
    print(f"  on_disconnect: {client.on_disconnect}")
    print(f"  suppress_exceptions: {client.suppress_exceptions}")

    # ── Connect ───────────────────────────────────────────────────────────────
    print(f"\n[{ts()}] [DIAG] Calling client.connect()...")
    try:
        client.connect(config.broker, config.port, keepalive=60)
    except Exception as e:
        print(f"[{ts()}] [DIAG] connect() raised: {e}")
        sys.exit(1)
    print(f"[{ts()}] [DIAG] client.connect() returned (TCP handshake done)")

    client.loop_start()
    print(f"[{ts()}] [DIAG] loop_start() called — background thread running")

    print(f"\n[{ts()}] [DIAG] Waiting for CONNACK (up to 10s)...")
    if not connected_event.wait(timeout=10.0):
        print(f"[{ts()}] [DIAG] CONNACK timeout after 10s!")
        client.loop_stop()
        return
    print(f"[{ts()}] [DIAG] CONNACK received")

    print(f"\n[{ts()}] [DIAG] Waiting for SUBACK (up to 10s)...")
    if not subscribed_event.wait(timeout=10.0):
        print(f"[{ts()}] [DIAG] SUBACK timeout after 10s!")
        client.loop_stop()
        return
    print(f"[{ts()}] [DIAG] SUBACK received — listening for {duration}s...")

    listen_start = time.time()
    heartbeat_interval = 15
    next_heartbeat = listen_start + heartbeat_interval

    try:
        while True:
            elapsed = time.time() - listen_start
            if elapsed >= duration:
                break
            now = time.time()
            if now >= next_heartbeat:
                remaining = duration - elapsed
                print(f"[{ts()}] [DIAG] Heartbeat: {int(elapsed)}s elapsed, "
                      f"{int(remaining)}s remaining, "
                      f"callback_count={callback_message_count}")
                next_heartbeat = now + heartbeat_interval
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"\n[{ts()}] [DIAG] Interrupted by user")
    finally:
        print(f"\n[{ts()}] [DIAG] Disconnecting...")
        client.disconnect()
        client.loop_stop()

    print(f"\n{'='*60}")
    print(f"DIAGNOSTIC SUMMARY")
    print(f"{'='*60}")
    print(f"Duration requested    : {duration}s")
    print(f"on_message() fired    : {callback_message_count} time(s)")
    print(f"Buffer count          : {buffer_message_count}")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnostic MQTT collector")
    parser.add_argument("--duration", "-d", type=int, default=120)
    args = parser.parse_args()
    run_diagnostic(args.duration)


if __name__ == "__main__":
    main()
