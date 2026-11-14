# Solserva daily report service

Collects the loggers' data from Yalgaar all day, stores it in a SQLite database (`data/solarpulse.db`), and at the end of the
day builds one HTML + PDF report per plant and e-mails it. Also builds reports from text files with `run_local.py`.

## Files
- `app/` : the code (`service.py` main loop, `yalgaar_client.py` collector, `store.py` database, `html_report.py` the report,
  `trips.py` trip loss, `calc.py` formulas, `weather.py` Open-Meteo, `mailer.py`).
- `scripts/` : `service.py` (run it), `run_local.py` (reports from `logger_data` text files), `import_raw.py` (one-time import),
  `devices.py`, `status.py`, `check_env.py`, `html_to_pdf.py`.
- `plants.json` (customer + plant details) and `loggers.json` (device ids + e-mails): **not in Git** (they hold names, addresses,
  phone numbers). Copy `plants.example.json` / `loggers.example.json` and fill them in.
- `.env`: secrets, **not in Git**. Copy `.env.example`.

## Run on a PC
```
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
copy .env.example .env          (then edit it)
copy plants.example.json plants.json
copy loggers.example.json loggers.json
.venv\Scripts\python scripts\check_env.py
.venv\Scripts\python scripts\service.py
```
Reports from collected text files: `py scripts\run_local.py --date 2026-11-14` (writes into `out\`).

## Run on a server (Docker)
```
docker compose build
docker compose run --rm -v $PWD/import:/app/import solarpulse python scripts/import_raw.py --data-dir /app/import   # once, optional
docker compose up -d
docker compose logs -f --tail 50
docker compose exec solarpulse python scripts/service.py --finalize 2026-11-12      # test the e-mail now
```
Only ONE machine may listen with a given `YALGAAR_UUID`, and the free Yalgaar plan counts every delivery, so stop other collectors
before the server goes live. Back up `data/solarpulse.db` daily (see the notes in the chat).

## Tests
```
py -m pytest -q tests
```
Old Track A engine (parser, validator, formulas, engine, PDF with reportlab) was removed from this repo; it is in the Git history
under the tag `old-engine`.
