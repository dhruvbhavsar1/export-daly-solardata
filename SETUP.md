# SolarPulse daily report: setup (one time, about 15 minutes)

## What runs every night
GitHub Actions starts at 18:45 IST -> connects to the broker with a fixed client id -> receives everything the broker
queued since the last run -> saves the day's readings in `data/raw/` -> builds the HTML report -> makes a PDF copy
-> emails both. No server needed. If GitHub starts late, nothing is lost (the broker keeps the messages).

## Steps
1. **Make the GitHub repo PRIVATE** (it stores customer readings). Upload this project WITHOUT `.env`, `.venv`, `raw_mqtt_data.txt`.
2. Repo -> Settings -> Secrets and variables -> Actions -> **Secrets**:
   `MQTT_BROKER, MQTT_PORT, MQTT_USERNAME, MQTT_PASSWORD, LOGGER_SERIAL, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO`
   (Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_PASS = a Google *app password*. MAIL_TO can be several addresses separated by commas.)
3. Same page -> **Variables** (not secret): `CLIENT_NAME, PLANT_ADDRESS, PLANT_ID, INVERTER, PLANT_KWP, TARIFF_INR`.
   Leave `PLANT_KWP` empty until you know it; the report then shows "N/A" instead of guessing.
4. Repo -> Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
5. Actions tab -> "Daily solar report" -> **Run workflow** once. Check the email and the "report" artifact.
   The FIRST run only creates the broker queue for this client id, so it may show little or no data. From the next night it is full.

## Rules that protect your data
- One client id = one reader. Do not run `collector.py`/`mqtt_raw_capture.py` on your PC with the same id as GitHub
  (set `MQTT_CLIENT_ID` as a Variable to choose GitHub's id). Two readers with one id steal each other's messages.
- Never paste `.env` or the password in chat. Your MQTT password is inside the zip you uploaded; change it when convenient.

## Add to `.gitignore`
raw_mqtt_data.txt
out/
