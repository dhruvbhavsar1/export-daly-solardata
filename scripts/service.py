"""SolarPulse service. Collects 07:00-18:00 IST from all logger channels, stores, then reports + emails.

  python scripts\\service.py                      run forever (the normal way)
  python scripts\\service.py --collect-now 10     collect for 10 minutes from now, then finalize today (test)
  python scripts\\service.py --finalize 2026-11-11   build summary/report/email for one stored day, then exit
Add --no-email to skip emails, --no-pdf to skip PDFs, --only 1380242000046,... to limit loggers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import logging
import logging.handlers
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import service as svc          # noqa: E402
from app import yalgaar_client as yc    # noqa: E402
from app.store import Store             # noqa: E402
from app.envfile import load_env_file  # noqa: E402


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--loggers", default=str(ROOT / "loggers.json"))
    ap.add_argument("--db", default=str(ROOT / "data" / "solarpulse.db"))
    ap.add_argument("--only")
    ap.add_argument("--finalize", metavar="YYYY-MM-DD")
    ap.add_argument("--collect-now", type=float, metavar="MINUTES")
    ap.add_argument("--no-email", action="store_true"); ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()

    (ROOT / "logs").mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger("solarpulse"); root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout),
              logging.handlers.RotatingFileHandler(ROOT / "logs" / "service.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")):
        h.setFormatter(fmt); root.addHandler(h)

    st = svc.Settings.from_env()
    st.send_email, st.make_pdf = not a.no_email, not a.no_pdf
    only = [x.strip() for x in a.only.split(",")] if a.only else None
    loggers = svc.load_loggers(Path(a.loggers), only)
    store = Store(a.db)
    channels = [c.strip() for c in (os.environ.get("YALGAAR_CHANNELS") or os.environ.get("YALGAAR_CHANNEL") or "1380242000046").split(",") if c.strip()]
    if st.send_email and not st.owner_mail:
        root.warning("MAIL_TO is empty: no emails will be sent"); st.send_email = False

    if a.finalize:
        rows = svc.finalize_day(dt.date.fromisoformat(a.finalize), loggers, store, st)
        for r in rows: print(r["serial"], r["status"], r["energy"])
        return 0
    try:
        cfg = yc.config_from_env(require_channel=False)   # channels come from loggers.json
    except ValueError as e:
        print("ERROR:", e); return 2
    if cfg is None:
        print("ERROR: YALGAAR_CLIENT_KEY is not set in .env"); return 2
    root.info("SolarPulse service starting: %d loggers, channel(s) %s, window %s-%s IST, database %s",
              len(loggers), ",".join(channels), st.window_start.strftime("%H:%M"), st.window_end.strftime("%H:%M"), a.db)
    if a.collect_now:
        until = svc.ist_now() + dt.timedelta(minutes=a.collect_now)
        svc.collect_window(cfg, channels, store, until)
        svc.finalize_day(svc.ist_now().date(), loggers, store, st)
        return 0
    try:
        svc.run_forever(cfg, channels, loggers, store, st)
    except KeyboardInterrupt:
        root.info("stopped by user")
    return 0


if __name__ == "__main__":
    sys.exit(main())
