@echo off
REM Starts the SolarPulse service and restarts it if it ever stops.
cd /d %~dp0
:loop
python scripts\service.py
echo Service stopped. Restarting in 30 seconds. Close this window to stop it for good.
timeout /t 30 /nobreak >nul
goto loop
