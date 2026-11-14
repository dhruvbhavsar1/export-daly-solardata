"""Quick look: what is stored, per customer logger and per device.   python scripts\\status.py [YYYY-MM-DD]"""
import datetime as dt, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.service import ist_now, load_loggers
from app.store import Store

day = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else ist_now().date()
store, now = Store(ROOT / "data" / "solarpulse.db"), ist_now()
loggers = load_loggers(ROOT / "loggers.json")
print(f"Now (IST): {now:%Y-%m-%d %H:%M}   day shown: {day}   database: {store.path}\n")
print(f"{'serial':14} {'name':26} {'device id':18} {'readings':>8} {'last reading':>13} {'age':>8}")
for lg in loggers:
    if not lg.macs:
        print(f"{lg.serial:14} {(lg.name or '')[:26]:26} {'(no device id set)':18}")
    for m in lg.macs:
        last = store.last_reading_time(m)
        print(f"{lg.serial:14} {(lg.name or '')[:26]:26} {m:18} {store.count_for_mac(m, day):>8} {last.strftime('%m-%d %H:%M') if last else '-':>13} {('-' if not last else str(int((now - last).total_seconds() // 60)) + ' min'):>8}")
assigned = {m for lg in loggers for m in lg.macs}
extra = {m: n for m, n in store.macs_for_day(day).items() if m not in assigned}
if extra:
    print("\nDevices seen but NOT assigned to a logger:")
    for m, n in sorted(extra.items()): print(f"  {m}: {n} readings   ->  python scripts\\devices.py")
print("\nDaily summaries (kept forever), last 5 days:")
for lg in loggers:
    for d in range(4, -1, -1):
        sm = store.get_summary(lg.serial, day - dt.timedelta(days=d))
        if sm:
            print(f"  {lg.serial} {sm['day']}  {sm['energy_kwh']:.1f} kWh  peak {sm['peak_kw']:.2f} kW  readings {sm['readings']}/{sm['expected']}")
