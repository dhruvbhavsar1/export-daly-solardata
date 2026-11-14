"""Which devices (headerMac) have been seen, and which customer logger owns each one?

  python scripts\\devices.py                      devices seen today
  python scripts\\devices.py 2026-11-11           devices seen on that day
  python scripts\\devices.py --assign 1380244000082=0608040905040703     give a device to a logger (edits loggers.json)

How to match: compare 'today kWh' here with the kWh each customer shows in the app at the same time.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.service import ist_now, load_loggers   # noqa: E402
from app.store import Store                     # noqa: E402

LOGGERS = ROOT / "loggers.json"


def assign(pair: str) -> int:
    serial, _, mac = pair.partition("=")
    serial, mac = serial.strip(), mac.strip()
    if not serial or not mac:
        print("Use:  --assign <logger serial>=<device id>"); return 2
    items = json.loads(LOGGERS.read_text(encoding="utf-8-sig"))
    hit = [i for i in items if str(i["serial"]).strip() == serial]
    if not hit:
        print(f"No logger with serial {serial} in loggers.json"); return 2
    for i in items:                                  # a device belongs to one logger only
        i["macs"] = [m for m in i.get("macs", []) if m != mac]
        if i.get("mac") == mac:
            i.pop("mac")
    hit[0].setdefault("macs", []).append(mac)
    LOGGERS.write_text(json.dumps(items, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OK: device {mac} now belongs to logger {serial} ({hit[0].get('name', '')})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("day", nargs="?"); ap.add_argument("--assign")
    a = ap.parse_args()
    if a.assign:
        return assign(a.assign)
    day = dt.date.fromisoformat(a.day) if a.day else ist_now().date()
    store = Store(ROOT / "data" / "solarpulse.db")
    owner = {m: lg for lg in load_loggers(LOGGERS) for m in lg.macs}
    now = ist_now()
    print(f"Devices seen on {day}  (now {now:%H:%M} IST)\n")
    print(f"{'device id':18} {'belongs to':32} {'readings':>8} {'newest':>7} {'age':>8} {'power kW':>9} {'today kWh':>9}")
    for d in store.devices_overview(day):
        lg = owner.get(d["mac"])
        last = dt.datetime.strptime(d["last"], "%Y-%m-%d %H:%M:%S")
        who = f"{lg.serial} {lg.name}"[:32] if lg else "-- NOT ASSIGNED --"
        print(f"{d['mac']:18} {who:32} {d['n']:>8} {last:%H:%M} {int((now - last).total_seconds() // 60):>6} m {d['last_p'] or 0:>9.2f} {d['max_e']:>9.1f}")
    print("\nTo assign:  python scripts\\devices.py --assign <logger serial>=<device id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
