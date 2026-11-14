#!/usr/bin/env python3
"""
Simple MQTT Raw Data Capture Script

Connects to the solar MQTT broker, authenticates, subscribes to the logger topic,
and saves the unaltered raw payload to raw_mqtt_data.txt.
"""

import os
import sys
import time
import datetime
import argparse
from pathlib import Path 

# Load environment variables from .env file if present
def load_env_file(env_path: Path):
    if not env_path.is_file():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Strip optional quotes around value
                if len(val) >= 2 and (
                    (val.startswith('"') and val.endswith('"')) or
                    (val.startswith("'") and val.endswith("'"))
                ):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"[WARNING] Could not read .env file: {e}")

# Search for .env in current script directory, cwd, or workspace
script_dir = Path(__file__).resolve().parent
load_env_file(script_dir / ".env")
load_env_file(Path.cwd() / ".env")
load_env_file(script_dir.parent / ".env")

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] paho-mqtt library is not installed. Please run: pip install paho-mqtt")
    sys.exit(1)

# Configuration from Environment Variables
BROKER = os.environ.get("MQTT_BROKER", "3.7.240.91")
PORT = int(os.environ.get("MQTT_PORT", 8883))
USERNAME = os.environ.get("MQTT_USERNAME", "solarbot")
PASSWORD = os.environ.get("MQTT_PASSWORD", "")
LOGGER_SERIAL = os.environ.get("LOGGER_SERIAL") or os.environ.get("MQTT_LOGGER_SERIAL", "1380242000046")
TOPIC = os.environ.get("MQTT_TOPIC", f"solar/{LOGGER_SERIAL}").strip()

DATA_FILE_PATH = script_dir / "raw_mqtt_data.txt"

# State tracking
message_count = 0
connected = False
subscription_done = False
connect_error = None


def save_raw_message(topic: str, payload_bytes: bytes):
    global message_count
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        payload_text = payload_bytes.decode("utf-8", errors="replace")
    except Exception:
        payload_text = str(payload_bytes)

    entry = (
        "========================================\n"
        f"TIMESTAMP: {timestamp}\n"
        f"TOPIC: {topic}\n"
        "PAYLOAD:\n"
        f"{payload_text}\n"
        "========================================\n\n"
    )

    with open(DATA_FILE_PATH, "a", encoding="utf-8") as f:
        f.write(entry)
        f.flush()

    message_count += 1


def main():
    global TOPIC, connected, subscription_done, connect_error

    parser = argparse.ArgumentParser(description="MQTT Raw Data Capture Test")
    parser.add_argument("--topic", help="MQTT topic to subscribe to", default=None)
    parser.add_argument("--timeout", type=int, default=120, help="Wait time in seconds for messages (default: 120)")
    args = parser.parse_args()

    if args.topic:
        TOPIC = args.topic.strip()

    print("[INFO] Connecting to MQTT broker...")

    # Fixed client ID (not timestamp-based) so clean_session=False can buffer
    # messages for us between sessions when the device publishes while offline.
    client_id = f"solar_raw_{LOGGER_SERIAL}"

    # Setup Paho Client (supporting both v2 Callback API and v1)
    # clean_session=False: broker retains queued messages between reconnects.
    has_v2 = hasattr(mqtt, "CallbackAPIVersion")
    if has_v2:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                             clean_session=False)
    else:
        client = mqtt.Client(client_id=client_id, clean_session=False)

    if USERNAME and PASSWORD:
        client.username_pw_set(USERNAME, PASSWORD)
    elif USERNAME:
        client.username_pw_set(USERNAME)

    def on_connect_v2(client, userdata, flags, reason_code, properties=None):
        global connected, connect_error
        if reason_code.is_failure:
            connect_error = str(reason_code)
            if "not authorized" in connect_error.lower() or "bad user name or password" in connect_error.lower():
                print("[ERROR] MQTT authentication failed.")
            else:
                print(f"[ERROR] Could not connect to MQTT broker. Reason: {reason_code}")
        else:
            connected = True
            print("[INFO] MQTT connection successful.")
            print("================================")
            print("MQTT CONNECTED")
            print("================================")
            print(f"Broker: {BROKER}")
            print(f"Port: {PORT}")
            print(f"Username: {USERNAME}")
            print(f"Logger: {LOGGER_SERIAL}")
            print("================================")

            if TOPIC:
                # QoS 1 ensures the broker re-delivers messages not yet acknowledged.
                result, mid = client.subscribe(TOPIC, qos=1)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    print(f"[INFO] Subscribed to: {TOPIC} (QoS 1)")
                else:
                    print(f"[ERROR] Subscription failed for topic: {TOPIC} (code: {result})")
            else:
                print("[WARNING] MQTT broker credentials are available, but the subscription topic is required.")

    def on_connect_v1(client, userdata, flags, rc):
        global connected, connect_error
        if rc == 0:
            connected = True
            print("[INFO] MQTT connection successful.")
            print("================================")
            print("MQTT CONNECTED")
            print("================================")
            print(f"Broker: {BROKER}")
            print(f"Port: {PORT}")
            print(f"Username: {USERNAME}")
            print(f"Logger: {LOGGER_SERIAL}")
            print("================================")

            if TOPIC:
                # QoS 1 ensures the broker re-delivers messages not yet acknowledged.
                result, mid = client.subscribe(TOPIC, qos=1)
                if result == mqtt.MQTT_ERR_SUCCESS:
                    print(f"[INFO] Subscribed to: {TOPIC} (QoS 1)")
                else:
                    print(f"[ERROR] Subscription failed for topic: {TOPIC} (code: {result})")
            else:
                print("[WARNING] MQTT broker credentials are available, but the subscription topic is required.")
        elif rc in (4, 5):
            print("[ERROR] MQTT authentication failed.")
            connect_error = f"Auth failed (code {rc})"
        else:
            print(f"[ERROR] Could not connect to MQTT broker. Code: {rc}")
            connect_error = f"Connection failed (code {rc})"

    def on_subscribe(client, userdata, mid, reason_codes=None, properties=None):
        global subscription_done
        subscription_done = True

    def on_message(client, userdata, message):
        payload_size = len(message.payload)
        print("[DATA] Message received")
        print(f"[DATA] Topic: {message.topic}")
        print(f"[DATA] Payload bytes: {payload_size}")
        save_raw_message(message.topic, message.payload)

    def on_disconnect(client, userdata, *args):
        # Graceful or unexpected disconnect
        rc = args[0] if args else None
        if not getattr(client, "_clean_exit", False):
            print(f"[ERROR] Unexpected disconnect from broker: {rc}")

    if has_v2:
        client.on_connect = on_connect_v2
    else:
        client.on_connect = on_connect_v1

    client.on_subscribe = on_subscribe
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    try:
        client.connect(BROKER, PORT, keepalive=60)
    except TimeoutError:
        print("[ERROR] Could not connect to MQTT broker. Network timeout.")
        return 1
    except ConnectionRefusedError:
        print("[ERROR] Could not connect to MQTT broker. Connection refused.")
        return 1
    except Exception as e:
        print(f"[ERROR] Could not connect to MQTT broker: {e}")
        return 1

    client.loop_start()

    # Wait for connection
    start_time = time.time()
    while not connected and not connect_error and (time.time() - start_time < 10):
        time.sleep(0.2)

    if connect_error:
        client.loop_stop()
        return 1

    if not connected:
        print("[ERROR] Could not connect to MQTT broker. Connection attempt timed out.")
        client.loop_stop()
        return 1

    if not TOPIC:
        print("\n[SUMMARY]")
        print("MQTT broker credentials are available, but the subscription topic is required.")
        print(f"Logger Serial: {LOGGER_SERIAL}")
        print("Please configure MQTT_TOPIC in .env or pass --topic <topic>")
        client._clean_exit = True
        client.loop_stop()
        client.disconnect()
        return 0

    # Wait for incoming messages
    wait_time = args.timeout
    listen_start = time.time()
    warning_logged = False

    try:
        while time.time() - listen_start < wait_time:
            time.sleep(1)
            elapsed = time.time() - listen_start
            if elapsed >= 10 and message_count == 0 and not warning_logged:
                print("[WARNING] Connected and subscribed successfully, but no messages received yet.")
                print("[WARNING] Solar inverters only publish during daylight hours (~07:00-18:30 IST).")
                print("[WARNING] If running after sunset, no data will arrive until the next morning.")
                warning_logged = True
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        client._clean_exit = True
        client.loop_stop()
        client.disconnect()

    print("\n================================")
    print("RUN SUMMARY")
    print("================================")
    print(f"Broker: {BROKER}:{PORT}")
    print(f"Topic: {TOPIC}")
    print(f"Messages Received: {message_count}")
    print(f"Output File: {DATA_FILE_PATH.name}")
    print("================================")

    return 0


if __name__ == "__main__":
    sys.exit(main())
