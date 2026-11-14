r"""One-time import of the text files collected on the PC (logger_data\raw_*.txt) into the service database.

Only COMPLETE days before today are imported, then each day is finalized (daily summary, trips, no e-mail, no PDF),
so "yesterday / last month / month totals" work from the first day on the server.
Safe to run again: readings are keyed by device + time, so nothing is stored twice.

    python scripts/import_raw.py --data-dir /app/import
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import service as svc                                   # noqa: E402
from app.html_report import parse_faults, parse_message           # noqa: E402
from app.store import Store                                       # noqa: E402
from app.envfile import load_env_file                       # noqa: E402

BLOCK = re.compile(r"={40}\r?\nTIMESTAMP.*?PAYLOAD:\r?\n(.*?)\r?\n={40}", re.S)


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--db", default=str(ROOT / "data" / "solarpulse.db"))
    ap.add_argument("--loggers", default=str(ROOT / "loggers.json"))
    a = ap.parse_args()
    st = svc.Settings.from_env(); st.send_email = st.make_pdf = False
    loggers, store, today = svc.load_loggers(Path(a.loggers)), Store(a.db), svc.ist_now().date()
    days, n_s, n_f = set(), 0, 0
    for f in sorted(Path(a.data_dir).glob("raw_*.txt")):
        samples, faults = [], []
        for pl in BLOCK.findall(f.read_text(encoding="utf-8", errors="replace")):
            try:
                o = json.loads(pl)
            except ValueError:
                continue
            s = parse_message(o)
            if s and s["t"].date() < today:
                samples.append(s)
            fa = parse_faults(o)
            if fa and fa["t"].date() < today:
                faults.append(fa)
        n_s += store.add_samples("import", samples)
        for fa in faults:
            store.add_fault(fa); n_f += 1
        days |= {s["t"].date() for s in samples}
        print(f"{f.name}: {len(samples)} readings, {len(faults)} fault messages")
    print(f"stored {n_s} new readings; finalizing {len(days)} day(s)")
    for d in sorted(days):
        rows = svc.finalize_day(d, loggers, store, st)
        print(d, ", ".join(f"{r['serial'][-4:]}={r['status']}" for r in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
