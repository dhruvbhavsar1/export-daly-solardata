#!/usr/bin/env python3
"""
Yalgaar capture, one file per device (headerMac).

All loggers publish on ONE channel. Each message has a "headerMac" that tells
which device sent it. This script saves every message into its own file:

    logger_data/raw_mac_<headerMac>.txt          (serial not known yet)
    logger_data/raw_<serial>.txt                 (after you fill MAC_TO_SERIAL)

When you know which MAC belongs to which logger serial, fill the dictionary
below, stop the script (Ctrl+C) and start it again. New messages then go to
files named by serial. Old files stay as they are.

Settings come from .env (never commit it):
    YALGAAR_CLIENT_KEY=ck-xxxxxxxxxxxxxxxx
    YALGAAR_CHANNEL=1380242000046
    YALGAAR_SSL=false
    YALGAAR_UUID_DEVICE=dhruv-device   (optional, must be different from the
                                        UUID of any other running script)

Run:
    py yalgaar_by_device.py --timeout 21600
"""

import os
import sys
import json
import time
import hashlib
import argparse
import datetime
import threading
from pathlib import Path

# Fill this later. Example:
#   "0501060802080608": "1380244000082",
MAC_TO_SERIAL = {
    "0608040905030704": "1380244000082",   # CHETAN POPATBHAI DEVANI
    "0501060802080608": "1380242000015",   # RAJ PACKAGING
    "0608040905040703": "1380244000091",   # RAJ TEXTURISOR
    "0501060806030409": "1380242000040",   # RAGHUVEER PACKAGING
    "0501060802070903": "1465242100028",   # Piyushkumar Dhameliya
    "0501060806030409": "1380242000046",   #
}

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
                if len(val) >= 2 and (
                    (val.startswith('"') and val.endswith('"')) or
                    (val.startswith("'") and val.endswith("'"))
                ):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as e:
        print(f"[WARNING] Could not read .env file: {e}")


script_dir = Path(__file__).resolve().parent
load_env_file(script_dir / ".env")
load_env_file(Path.cwd() / ".env")
load_env_file(script_dir.parent / ".env")

CLIENT_KEY = os.environ.get("YALGAAR_CLIENT_KEY", "").strip()
CHANNEL = os.environ.get("YALGAAR_CHANNEL", "1380242000046").strip()
UUID = os.environ.get("YALGAAR_UUID_DEVICE", "dhruv-device").strip()
USE_SSL = os.environ.get("YALGAAR_SSL", "false").strip().lower() in ("1", "true", "yes")

OUT_DIR = script_dir / "logger_data"
OUT_DIR.mkdir(exist_ok=True)

counts = {}
last_seen = {}
duplicate_count = 0
seen_hashes = set()
lock = threading.Lock()


def extract_header_mac(text: str) -> str:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return str(obj.get("headerMac", "unknown"))
    except Exception:
        pass
    return "unknown"


def save_message(channel: str, text: str):
    """Save one message. Returns the file name, or None if duplicate."""
    global duplicate_count
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mac = extract_header_mac(text)
    safe_mac = "".join(c for c in mac if c.isalnum() or c in "-_") or "unknown"

    serial = MAC_TO_SERIAL.get(mac)
    fname = f"raw_{serial}.txt" if serial else f"raw_mac_{safe_mac}.txt"

    digest = hashlib.sha256((mac + "|" + text).encode("utf-8", "replace")).hexdigest()
    with lock:
        if digest in seen_hashes:
            duplicate_count += 1
            return None
        seen_hashes.add(digest)
        if len(seen_hashes) > 100000:
            seen_hashes.clear()

        entry = (
            "========================================\n"
            f"TIMESTAMP: {now}\n"
            f"CHANNEL: {channel}\n"
            f"HEADER_MAC: {mac}\n"
            "PAYLOAD:\n"
            f"{text}\n"
            "========================================\n\n"
        )
        with open(OUT_DIR / fname, "a", encoding="utf-8") as f:
            f.write(entry)
            f.flush()
        counts[fname] = counts.get(fname, 0) + 1
        last_seen[fname] = now
    return fname


def print_summary():
    print("\n================================")
    print("RUN SUMMARY")
    print("================================")
    for fname in sorted(counts):
        print(f"  {fname}: {counts[fname]} messages, last {last_seen[fname]}")
    print(f"Duplicates skipped: {duplicate_count}")
    print(f"Files are in: {OUT_DIR}")
    print("================================")


def main():
    parser = argparse.ArgumentParser(description="Yalgaar capture by device")
    parser.add_argument("--timeout", type=int, default=0,
                        help="Stop after N seconds (0 = run until Ctrl+C)")
    args = parser.parse_args()

    if not CLIENT_KEY:
        print("[ERROR] YALGAAR_CLIENT_KEY is not set. Add it to your .env file.")
        return 1

    try:
        from yalgaar import Yalgaar
    except ImportError as e:
        print(f"[ERROR] Could not import the Yalgaar SDK: {e}")
        print("        Run: py -m pip install pyYalgaar pycryptodome")
        return 1

    def on_connect(code, text):
        if code == 0:
            print("[INFO] Yalgaar connection successful.")
        else:
            print(f"[ERROR] Yalgaar connection problem: {code} {text}")

    def on_message(ch, data):
        text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data)
        fname = save_message(CHANNEL, text)
        if fname is None:
            print("[DUP] duplicate skipped")
        else:
            print(f"[DATA] {fname}  chars={len(text)}  total_for_this={counts[fname]}")

    def on_presence(data):
        pass

    def on_error(data):
        print(f"[ERROR] Yalgaar: {data}")

    print("[INFO] Connecting to Yalgaar...")
    print(f"[INFO] Channel: {CHANNEL}   SSL: {USE_SSL}   UUID: {UUID}")
    print(f"[INFO] Mapped devices: {len(MAC_TO_SERIAL)}")

    y = Yalgaar()
    y.yalgaarConnect(CLIENT_KEY, USE_SSL, on_connect, UUID, None, None)
    y.yalgaarSubscribe(CHANNEL, on_message, on_presence, on_error)

    print("[INFO] Listening. Press Ctrl+C to stop.")

    if args.timeout > 0:
        def stop_later():
            time.sleep(args.timeout)
            print_summary()
            os._exit(0)
        threading.Thread(target=stop_later, daemon=True).start()

    try:
        y.loop_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    print_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())  