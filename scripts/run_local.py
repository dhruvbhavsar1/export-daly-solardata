r"""Build one daily report per logger from the files collected on this PC (no server needed).

Reads  : every <DATA_DIR>\raw_*.txt, grouped by device id (headerMac), matched to plants.json by "mac"
Writes : out\solar_report_<serial>_<day>.html
Plant details (client, kWp, plan, ...) come from plants.json. Every day found in the files is used for the
yesterday / month / last-year comparisons, the month graphs and the trip log. No passwords are used.

Usage:  py scripts\run_local.py                 (today, IST)
        py scripts\run_local.py --date 2026-11-12
        py scripts\run_local.py --serial 1380242000040
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.html_report import build_daily_html, parse_message, parse_faults, dedupe   # noqa: E402
from app.service import LoggerCfg, Settings, site_for                               # noqa: E402
from app.trips import detect_trips                                                  # noqa: E402
from app.weather import get_weather                                                 # noqa: E402

BLOCK = re.compile(r"={40}\r?\nTIMESTAMP.*?PAYLOAD:\r?\n(.*?)\r?\n={40}", re.S)
DEFAULT_DIR = r"C:\Solar-projact\mqtt_raw_test\logger_data"


def load_env() -> None:
    f = ROOT / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def read_all(data_dir: Path) -> dict[str, tuple[list, list]]:
    """Read EVERY raw_*.txt file and group the messages by device id (headerMac), so a wrong file name cannot mix plants."""
    by_mac: dict[str, tuple[list, list]] = {}
    for f in sorted(data_dir.glob("raw_*.txt")):
        for pl in BLOCK.findall(f.read_text(encoding="utf-8", errors="replace")):
            try:
                o = json.loads(pl)
            except Exception:
                continue
            sm, fl = by_mac.setdefault(str(o.get("headerMac") or ""), ([], []))
            s = parse_message(o)
            if s:
                sm.append(s)
            fa = parse_faults(o)
            if fa:
                fl.append(fa)
    return {m: (dedupe(sm), list({(x["t"], tuple(x["active"])): x for x in fl}.values())) for m, (sm, fl) in by_mac.items()}


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--serial")
    ap.add_argument("--data-dir", default=os.environ.get("DATA_DIR", DEFAULT_DIR))
    ap.add_argument("--plants", default=str(ROOT / "plants.json"))
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)).date()
    data_dir = Path(a.data_dir)
    if not data_dir.is_dir():
        print("ERROR: data folder not found:", data_dir); return 2
    out_dir = ROOT / "out"; out_dir.mkdir(exist_ok=True)
    st = Settings.from_env()
    data = read_all(data_dir)
    known = {str(p.get('mac')) for p in json.loads(Path(a.plants).read_text(encoding='utf-8-sig'))}
    for m, (sm, _) in data.items():
        if m not in known:
            print(f"NOTE: device {m} ({len(sm)} readings) is not in plants.json, so it gets no report")
    for p in json.loads(Path(a.plants).read_text(encoding="utf-8-sig")):
        if a.serial and p["serial"] != a.serial:
            continue
        samples, faults = data.get(str(p.get("mac") or "-"), ([], []))
        site = site_for(LoggerCfg(serial=p["serial"], name=p["client"], address=p.get("address") or "-", kwp=p.get("kwp"), plant=p), st)
        by_day: dict[dt.date, list] = {}
        for x in samples:
            by_day.setdefault(x["t"].date(), []).append(x)
        hist = {d: max(x["e_kwh"] for x in v) for d, v in by_day.items() if d <= day}
        fday = lambda d: [f for f in faults if f["t"].date() == d]
        month_trips = [e for d in sorted(by_day) if d.replace(day=1) == day.replace(day=1) and d < day
                       for e in detect_trips(by_day[d], fday(d), d, site.tariff)]
        today = by_day.get(day, [])
        html_ = build_daily_html(today, day, site, history=hist, faults=fday(day) or None,
                                 trips=detect_trips(today, fday(day), day, site.tariff), month_trips=month_trips,
                                 weather=get_weather(site.lat, site.lon, day))
        out = out_dir / f"solar_report_{p['serial']}_{day}.html"
        out.write_text(html_, encoding="utf-8")
        print(f"{p['serial']} {p['client']}: {len(today)} readings -> {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())