# SolarPulse daily report: setup

## How it works
Yalgaar (broker api.yalgaar.io) receives the logger messages. GitHub Actions connects to Yalgaar
with your client key, collects the day's readings 5 times (09:00, 11:30, 14:00, 16:30 IST), and at
18:45 IST collects once more, builds the HTML page + PDF and emails both through Gmail.
Readings are saved in `data/raw/` in the PRIVATE repo, so each run adds to the day.

Yalgaar login = MQTT client id `<client key>/<uuid>`. No username/password.

## GitHub settings (Settings -> Secrets and variables -> Actions)
Secrets:
- `YALGAAR_CLIENT_KEY`  (new)
- `LOGGER_SERIAL` = 1380242000046
- `SMTP_HOST` smtp.gmail.com, `SMTP_PORT` 587, `SMTP_USER`, `SMTP_PASS` (Google app password), `MAIL_FROM`
- `MAIL_TO`  (several addresses separated by commas, no spaces)
Variables:
- `YALGAAR_UUID` = solarpulse-gha   (must NOT be the same as the uuid on your PC)
- `CLIENT_NAME`, `PLANT_ADDRESS`, `PLANT_ID`, `INVERTER`, `TARIFF_INR`=7   (`PLANT_KWP` when known)
Old secrets `MQTT_BROKER/PORT/USERNAME/PASSWORD/TOPIC` are no longer used. You may delete them.
Settings -> Actions -> General -> Workflow permissions -> Read and write.

## First test on your PC (2 minutes)
1. Copy `.env.example` to `.env` and fill YALGAAR_CLIENT_KEY (and plant details). Keep `YALGAAR_UUID=solarpulse-pc`.
2. `python -m pip install -r requirements.txt`
3. `python scripts\run_daily.py --collect-only --max 90`
4. Read the line `broker answer to login:`. Good = `0 = Connection successful`.
   Then the `collect:` line shows live messages and history_msgs.
5. Close the PC run before you start a GitHub run (the account may allow only one connection).

## Yalgaar error numbers you may see
102 invalid client id, 103 key not registered, 105 key not active, 107 connection limit reached
(another connection is using the key), 112 storage not enabled (history unavailable, not fatal),
118 message limit reached, 125 subscribe disabled for this uuid.

## Notes
- History from Yalgaar is limited to the last 100 messages (about 8 hours of this logger). That is why
  the job collects several times a day.
- If a message layout is different from what we expect, the run prints its shape (no values) and
  saves `out/unparsed_sample.txt` (artifact `report` on the run page). Send me that file.
- Email apps remove JavaScript, so the interactive chart is in the attached HTML file.
