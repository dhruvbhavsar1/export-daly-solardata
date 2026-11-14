"""Basic weather and sun times for the report, from Open-Meteo (free, no API key, non-commercial use, CC BY 4.0).

One call per (location, day), cached in memory. Any failure returns None, and the report then says
"Weather not available" - it never blocks or breaks the report. All plants are in Surat, so the default
location is Surat; a plant can set its own "lat" / "lon" / "city" in plants.json.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request
from typing import Any, Optional

SURAT = (21.17, 72.83)
URL = "https://api.open-meteo.com/v1/forecast"
_cache: dict[tuple, dict] = {}


def parse(data: dict) -> Optional[dict[str, Any]]:
    try:
        d = data["daily"]
        sr, ss = dt.datetime.fromisoformat(d["sunrise"][0]), dt.datetime.fromisoformat(d["sunset"][0])
        hum = [h for t, h in zip(data["hourly"]["time"], data["hourly"]["relative_humidity_2m"])
               if h is not None and sr <= dt.datetime.fromisoformat(t) <= ss]
        hum = hum or [h for h in data["hourly"]["relative_humidity_2m"] if h is not None]
        return {"tmax": d["temperature_2m_max"][0], "tmin": d["temperature_2m_min"][0],
                "humidity": sum(hum) / len(hum) if hum else None, "sunrise": sr, "sunset": ss,
                "daylight_h": (ss - sr).total_seconds() / 3600}
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def get_weather(lat: float, lon: float, day: dt.date, timeout: float = 10.0) -> Optional[dict[str, Any]]:
    key = (round(lat, 2), round(lon, 2), day)
    if key in _cache:
        return _cache[key]
    q = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "timezone": "Asia/Kolkata", "start_date": str(day), "end_date": str(day),
                                "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset", "hourly": "relative_humidity_2m"})
    for _ in range(2):
        try:
            req = urllib.request.Request(f"{URL}?{q}", headers={"User-Agent": "solserva-report/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                w = parse(json.loads(r.read().decode("utf-8")))
            if w:
                _cache[key] = w
                return w
        except Exception:
            continue
    return None