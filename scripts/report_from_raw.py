"""Offline: build the daily HTML report from a raw_mqtt_data.txt capture (for testing/demo)."""
import argparse, datetime as dt, json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.html_report import Site, build_daily_html, parse_message, dedupe

ap = argparse.ArgumentParser()
ap.add_argument("--raw", default="raw_mqtt_data.txt"); ap.add_argument("--date", required=True)
ap.add_argument("--out", default="out/daily_report.html")
a = ap.parse_args()
day = dt.date.fromisoformat(a.date)
txt = Path(a.raw).read_text(encoding="utf-8", errors="ignore")
samples = []
for p in re.findall(r"PAYLOAD:\r?\n(\{.*?\})\r?\n=+", txt, re.S):
    try: s = parse_message(json.loads(p))
    except Exception: continue
    if s: samples.append(s)
samples = dedupe(samples)
def day_energy(d):
    v = [x["e_kwh"] for x in samples if x["t"].date() == d]; return max(v) if v else None
prev = day_energy(day - dt.timedelta(days=1))
month = sum(filter(None, (day_energy(d) for d in {x["t"].date() for x in samples} if d.month == day.month and d <= day)))
site = Site(client="Demo Client (name not set)", plant_id="LOGGER-1380242000046", serial="1380242000046",
            address="— (not set)", inverter="— (not set)")
Path(a.out).parent.mkdir(parents=True, exist_ok=True)
Path(a.out).write_text(build_daily_html(samples, day, site, prev, month), encoding="utf-8")
print("wrote", a.out, "| samples that day:", len([x for x in samples if x['t'].date()==day]), "| prev:", prev, "| month:", round(month,1))
