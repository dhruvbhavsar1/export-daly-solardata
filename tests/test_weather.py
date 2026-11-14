import datetime as dt
import json

from app import weather
from app.html_report import Site, build_daily_html
from tests.test_trips import DAY, day_with_stop

SAMPLE = {"daily": {"time": ["2026-11-12"], "temperature_2m_max": [33.4], "temperature_2m_min": [21.0],
                    "sunrise": ["2026-11-12T06:53"], "sunset": ["2026-11-12T18:02"]},
          "hourly": {"time": [f"2026-11-12T{h:02d}:00" for h in range(24)], "relative_humidity_2m": [50] * 24}}


def test_parse_and_failures():
    w = weather.parse(SAMPLE)
    assert w["tmax"] == 33.4 and w["humidity"] == 50 and w["sunrise"].strftime("%H:%M") == "06:53" and abs(w["daylight_h"] - 11.15) < 0.01
    assert weather.parse({}) is None and weather.parse({"daily": {"sunrise": []}}) is None


def test_get_weather_uses_cache_and_survives_errors(monkeypatch):
    weather._cache.clear()
    calls = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return json.dumps(SAMPLE).encode()
    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: calls.append(1) or R())
    assert weather.get_weather(21.17, 72.83, DAY)["tmax"] == 33.4
    weather.get_weather(21.17, 72.83, DAY)
    assert len(calls) == 1
    weather._cache.clear()
    monkeypatch.setattr(weather.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    assert weather.get_weather(21.17, 72.83, DAY) is None


def test_report_shows_weather_or_a_note():
    s, f = day_with_stop()
    site = Site(client="X", address="Y", plant_id="P", serial="S", kwp=40)
    h = build_daily_html(s, DAY, site, faults=f, weather=weather.parse(SAMPLE))
    assert "33° / 21°C" in h and "06:53" in h and "18:02" in h and "Open-Meteo.com" in h
    assert "Weather not available" in build_daily_html(s, DAY, site, faults=f)