"""SQLite storage: raw 5-minute readings (kept N days) + one permanent daily summary per logger.

A reading is identified by the DEVICE (headerMac) and the logger's own time. The Yalgaar channel it arrived on
is stored for information only: one channel can carry many devices, and the message itself has no serial.
Which device belongs to which customer logger is set in loggers.json ("macs").
All times are IST wall-clock (the logger's own clock), stored as 'YYYY-MM-DD HH:MM:SS'.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings(
  mac TEXT NOT NULL, t TEXT NOT NULL, channel TEXT,
  p_kw REAL, e_kwh REAL, total_kwh REAL, v REAL, i_a REAL, hz REAL, pf REAL,
  mppt_v REAL, mppt_a REAL, status INTEGER, recv_at TEXT,
  PRIMARY KEY(mac, t));
CREATE INDEX IF NOT EXISTS ix_readings_t ON readings(t);
CREATE TABLE IF NOT EXISTS daily_summary(
  serial TEXT NOT NULL, day TEXT NOT NULL, mac TEXT,
  energy_kwh REAL, peak_kw REAL, peak_time TEXT, first_light TEXT, last_light TEXT,
  readings INTEGER, expected INTEGER, integ_kwh REAL, mismatch_pct REAL,
  value_inr REAL, co2_kg REAL, gaps INTEGER, created_at TEXT,
  PRIMARY KEY(serial, day));
CREATE TABLE IF NOT EXISTS jobs(day TEXT PRIMARY KEY, finalized_at TEXT);
CREATE TABLE IF NOT EXISTS faults(mac TEXT NOT NULL, t TEXT NOT NULL, flags TEXT, PRIMARY KEY(mac, t));
CREATE TABLE IF NOT EXISTS trip_events(
  serial TEXT NOT NULL, day TEXT NOT NULL, start TEXT NOT NULL, end TEXT, kind TEXT, codes TEXT,
  minutes REAL, loss_kwh REAL, loss_inr REAL, PRIMARY KEY(serial, start));
"""
_F = ("p_kw", "e_kwh", "total_kwh", "v", "i_a", "hz", "pf", "mppt_v", "mppt_a", "status")
_ISO = "%Y-%m-%d %H:%M:%S"


def _ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30)


def _iso(x: Optional[dt.datetime]) -> Optional[str]:
    return x.strftime(_ISO) if x else None


def _move_old_trial_db(path: str) -> None:
    """The first trial version stored readings per channel ('serial' column). Keep that file aside."""
    if path == ":memory:" or not Path(path).exists():
        return
    try:
        c = sqlite3.connect(path)
        old = c.execute("SELECT 1 FROM pragma_table_info('readings') WHERE name='serial'").fetchone()
        c.close()
    except sqlite3.Error:
        return
    if old:
        stamp = _ist_now().strftime("%Y%m%d_%H%M%S")
        for ext in ("", "-wal", "-shm"):
            if Path(path + ext).exists():
                os.replace(path + ext, f"{path}.old_trial_{stamp}{ext}")


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            _move_old_trial_db(self.path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---- readings
    def add_samples(self, channel: str, samples: Iterable[dict]) -> int:
        now = _iso(_ist_now())
        rows = [(s.get("mac") or "unknown", _iso(s["t"]), channel, *(s.get(k) for k in _F), now) for s in samples]
        with self._lock:
            before = self.db.total_changes
            self.db.executemany(
                "INSERT OR IGNORE INTO readings(mac,t,channel,p_kw,e_kwh,total_kwh,v,i_a,hz,pf,mppt_v,mppt_a,status,recv_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self.db.commit()
            return self.db.total_changes - before

    @staticmethod
    def _bounds(day: dt.date) -> tuple[str, str]:
        return f"{day} 00:00:00", f"{day + dt.timedelta(days=1)} 00:00:00"

    def macs_for_day(self, day: dt.date) -> Counter:
        a, b = self._bounds(day)
        with self._lock:
            cur = self.db.execute("SELECT mac, COUNT(*) FROM readings WHERE t>=? AND t<? GROUP BY mac", (a, b))
            return Counter(dict(cur.fetchall()))

    def count_for_mac(self, mac: str, day: dt.date) -> int:
        a, b = self._bounds(day)
        with self._lock:
            return self.db.execute("SELECT COUNT(*) FROM readings WHERE mac=? AND t>=? AND t<?", (mac, a, b)).fetchone()[0]

    def day_samples(self, mac: str, day: dt.date) -> list[dict]:
        a, b = self._bounds(day)
        q = f"SELECT mac,t,{','.join(_F)} FROM readings WHERE mac=? AND t>=? AND t<? ORDER BY t"
        with self._lock:
            rows = self.db.execute(q, (mac, a, b)).fetchall()
        return [dict(zip(("mac", "t") + _F, r), t=dt.datetime.strptime(r[1], _ISO)) for r in rows]

    def last_reading_time(self, mac: Optional[str] = None) -> Optional[dt.datetime]:
        with self._lock:
            if mac:
                r = self.db.execute("SELECT MAX(t) FROM readings WHERE mac=?", (mac,)).fetchone()
            else:
                r = self.db.execute("SELECT MAX(t) FROM readings").fetchone()
        return dt.datetime.strptime(r[0], _ISO) if r and r[0] else None

    def devices_overview(self, day: dt.date) -> list[dict]:
        """One row per device seen that day: count, first/last time, power at last reading, highest daily kWh."""
        a, b = self._bounds(day)
        with self._lock:
            rows = self.db.execute("SELECT mac,t,p_kw,e_kwh,channel FROM readings WHERE t>=? AND t<? ORDER BY t", (a, b)).fetchall()
        out: dict[str, dict] = {}
        for mac, t, p, e, ch in rows:
            d = out.setdefault(mac, {"mac": mac, "n": 0, "first": t, "last": t, "last_p": p, "max_e": e or 0, "channel": ch})
            d["n"] += 1
            d["last"], d["last_p"], d["max_e"] = t, p, max(d["max_e"], e or 0)
        return sorted(out.values(), key=lambda d: d["mac"])

    # ---- fault messages (functionId 15) and trips
    def add_fault(self, f: dict) -> None:
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO faults(mac,t,flags) VALUES(?,?,?)",
                            (f.get("mac") or "unknown", _iso(f["t"]), json.dumps(f.get("active", []))))
            self.db.commit()

    def day_faults(self, mac: str, day: dt.date) -> list[dict]:
        a, b = self._bounds(day)
        with self._lock:
            rows = self.db.execute("SELECT t,flags FROM faults WHERE mac=? AND t>=? AND t<? ORDER BY t", (mac, a, b)).fetchall()
        return [{"t": dt.datetime.strptime(t, _ISO), "mac": mac, "active": json.loads(fl or "[]")} for t, fl in rows]

    def energy_history(self, serial: str) -> dict[dt.date, float]:
        with self._lock:
            rows = self.db.execute("SELECT day, energy_kwh FROM daily_summary WHERE serial=? AND energy_kwh IS NOT NULL", (serial,)).fetchall()
        return {dt.date.fromisoformat(d): float(e) for d, e in rows}

    def replace_trips(self, serial: str, day: dt.date, events: list[dict]) -> None:
        with self._lock:
            self.db.execute("DELETE FROM trip_events WHERE serial=? AND day=?", (serial, str(day)))
            self.db.executemany("INSERT OR REPLACE INTO trip_events VALUES(?,?,?,?,?,?,?,?,?)",
                [(serial, str(day), _iso(e["start"]), _iso(e["end"]), e["kind"], json.dumps(e["codes"]), e["minutes"], e["loss_kwh"], e["loss_inr"]) for e in events])
            self.db.commit()

    def trips_before(self, serial: str, day: dt.date) -> list[dict]:
        """Stored trip events of the earlier days of this month."""
        with self._lock:
            rows = self.db.execute("SELECT start,end,kind,codes,minutes,loss_kwh,loss_inr FROM trip_events WHERE serial=? AND day>=? AND day<?",
                                   (serial, str(day.replace(day=1)), str(day))).fetchall()
        return [{"start": dt.datetime.strptime(a, _ISO), "end": dt.datetime.strptime(b, _ISO), "kind": k, "codes": json.loads(c or "[]"),
                 "minutes": mi, "loss_kwh": kw, "loss_inr": inr} for a, b, k, c, mi, kw, inr in rows]

    def prune(self, keep_days: int, today: dt.date) -> int:
        cutoff = f"{today - dt.timedelta(days=keep_days)} 00:00:00"
        with self._lock:
            self.db.execute("DELETE FROM faults WHERE t<?", (cutoff,))
            n = self.db.execute("DELETE FROM readings WHERE t<?", (cutoff,)).rowcount
            self.db.commit()
        return n

    # ---- permanent daily summary (one row per customer logger per day)
    def upsert_summary(self, serial: str, day: dt.date, mac: str, m: dict, expected: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO daily_summary VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (serial, str(day), mac, m.get("energy"), m.get("peak_kw"), _iso(m.get("peak_t")),
                 _iso(m.get("first")), _iso(m.get("last")), m.get("n"), expected, m.get("integ"),
                 m.get("mismatch_pct"), m.get("value_inr"), m.get("co2"), m.get("gaps"), _iso(_ist_now())))
            self.db.commit()

    def get_summary(self, serial: str, day: dt.date) -> Optional[dict]:
        with self._lock:
            cur = self.db.execute("SELECT * FROM daily_summary WHERE serial=? AND day=?", (serial, str(day)))
            r = cur.fetchone()
            return dict(zip([c[0] for c in cur.description], r)) if r else None

    def prev_energy(self, serial: str, day: dt.date) -> Optional[float]:
        s = self.get_summary(serial, day - dt.timedelta(days=1))
        return s["energy_kwh"] if s else None

    def month_energy_before(self, serial: str, day: dt.date) -> float:
        with self._lock:
            r = self.db.execute("SELECT COALESCE(SUM(energy_kwh),0) FROM daily_summary WHERE serial=? AND day>=? AND day<?",
                                (serial, str(day.replace(day=1)), str(day))).fetchone()
        return float(r[0])

    # ---- job markers
    def is_finalized(self, day: dt.date) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM jobs WHERE day=?", (str(day),)).fetchone() is not None

    def mark_finalized(self, day: dt.date) -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES(?,?)", (str(day), _iso(_ist_now())))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()