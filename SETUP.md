# SolarPulse service: setup (Windows PC first, server later)

## What it does
`scripts\service.py` runs all day. From 07:00 IST it keeps one Yalgaar connection per logger channel,
stores every reading in `data\solarpulse.db` (SQLite), and at 18:00 IST it:
1. saves one permanent daily summary per logger,
2. builds the HTML + PDF report per logger,
3. emails the reports and one short summary of all loggers to MAIL_TO,
4. deletes raw readings older than 90 days (summaries are never deleted).
If a connection drops, it reconnects by itself. If the PC starts after 18:00, it finalizes the day at once.
The GitHub workflow is now manual only (backup). Do not run both with the same YALGAAR_UUID.

## Step 1: files
Extract the zip over your project folder. Your `.env` stays (the zip has none). Your old test database is moved aside automatically.
Delete `.github\workflows\net-test.yml` and `broker-diag.yml` if they still exist.

## Step 2: .env  (copy `.env.example`, fill the blanks; keep it on this machine only)
- `YALGAAR_CLIENT_KEY`, and `YALGAAR_UUID=solarpulse-pc`
- `SMTP_USER`, `SMTP_PASS` (Gmail app password), `MAIL_FROM`, `MAIL_TO`
- Keep `SEND_TO_CLIENTS=false` until you are happy. Then fill each logger's `emails` in `loggers.json` and set it to true.

## Step 3: 10-minute trial (no emails), then match devices to customers
    python -m pip install -r requirements.txt
    python scripts\service.py --collect-now 10 --no-email --no-pdf
    python scripts\devices.py
All your devices publish on ONE Yalgaar channel (1380242000046) and a message has no serial, only a device id.
`devices.py` lists every device id with its newest power and "today kWh". Compare "today kWh" with what each
customer shows in the app, then give each device to its customer:
    python scripts\devices.py --assign 1380244000082=<device id>
(The test logger 1380242000046 is already assigned.) `python scripts\status.py` shows what is stored per logger.

## Step 4: finish the first day by hand (no emails)
    python scripts\service.py --finalize 2026-11-11 --no-email --no-pdf
Open `out\solar_report_<serial>_<date>.html`.

## Step 5: run it for real
    run_service.bat
Leave the window open. To start it when you log in to Windows:
    schtasks /Create /TN "SolarPulse" /TR "C:\export-daly-solardata\run_service.bat" /SC ONLOGON
(Use your real folder path.) Turn off sleep in Windows power settings: the PC must stay on 07:00-18:00.
Log file: `logs\service.log`. Check any time with `python scripts\status.py`.

## Rebuild a day by hand
    python scripts\service.py --finalize 2026-11-11 --no-pdf

## Server later (same code)
Docker: `docker compose up -d --build` on a small Linux server, with the same `.env` and `YALGAAR_UUID=solarpulse-srv`.
(The Docker files are not tested yet.)

## Rules
- One listener per UUID. PC, server and GitHub each need their own UUID, and only one should run.
- Yalgaar free plan: 5,000 messages a day, each message counted when sent and when delivered. Watch the dashboard.
- Never paste `.env`, the client key or the Gmail password in chat.
