"""Trip detection and estimated energy loss.

A TRIP = the inverter stops producing (<= 0.05 kW) during the day while the sun is up.
* The type comes from the fault flags (functionId 15) that arrive around the stop:
  - Plant & Grid Trip       : grid, AC breaker, panel / DC side (codes from trip-code.txt)
  - Internal Inverter Trip  : hardware, temperature, DC bus, relay (codes from trip-code.txt)
  - Unclassified stop       : the inverter stopped but no fault flag was received (shown separately)
* Lost energy is an ESTIMATE: power that would have been produced, taken as a straight line between the last
  reading before and the first reading after the stop (0 kW at 07:00 and 18:00 if there is no neighbour).

FLAG_CODES maps the 18 fault-flag names of the logger to the codes in trip-code.txt. The last field says if the
match is certain. Edit this table if a flag means something else on your inverters.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

GRID, INTERNAL, OTHER = "Plant & Grid Trip", "Internal Inverter Trip", "Unclassified stop"

# flag: (type, error code, alarm id, meaning, certain)
FLAG_CODES: dict[str, tuple[str, str, str, str, bool]] = {
    "NG":   (GRID, "NO-Grid", "1015", "No AC grid voltage", True),
    "GOV":  (GRID, "OV-G-V", "1010", "Grid voltage too high", True),
    "GUV":  (GRID, "UN-G-V", "1011", "Grid voltage too low", True),
    "GOF":  (GRID, "OV-G-F", "1012", "Grid frequency too high", True),
    "GUF":  (GRID, "UN-G-F", "1013", "Grid frequency too low", True),
    "PU":   (GRID, "G-PHASE", "1016", "Phase imbalance or phase loss", True),
    "DIF":  (GRID, "PV ISO-PRO", "1030", "Low PV insulation resistance", True),
    "DOV":  (GRID, "OV-DC", "1020", "DC input voltage too high", True),
    "GF":   (GRID, "ILeak-PRO", "1031-1034", "Ground fault / leakage", False),
    "GFCI": (GRID, "ILeak-PRO", "1031-1034", "Ground leakage current", False),
    "DUV":  (GRID, "DC-LOW", "-", "DC input voltage too low (not in table)", False),
    "DUC":  (GRID, "DC-LOW", "-", "DC input current too low (not in table)", False),
    "HT":   (INTERNAL, "OV-TEM", "1030/1040", "Inverter too hot", True),
    "GRC":  (INTERNAL, "RelayChk-FAIL", "1035", "AC relay self-check failed", True),
    "DBOV": (INTERNAL, "OV-BUS", "1021", "DC bus voltage too high", True),
    "DBUV": (INTERNAL, "UN-BUS", "1022", "DC bus voltage too low", True),
    "DBU":  (INTERNAL, "OV-BUS / UN-BUS", "1021-1022", "DC bus unbalance", False),
    "DOC":  (INTERNAL, "IGBT-OV-I", "1048", "Power module overcurrent", False),
}
STOP_KW = 0.05
CADENCE = 300


def code_label(flag: str) -> str:
    c = FLAG_CODES.get(flag)
    return flag if not c else f"{c[1]} ({c[2]})" if c[2] != "-" else c[1]


def _expected(t: dt.datetime, before, after, a0: dt.datetime, a1: dt.datetime) -> float:
    pts = [(a0, 0.0)]
    for n in (before, after):
        if n is not None and a0 < n["t"] < a1:
            pts.append((n["t"], n["p_kw"]))
    pts.append((a1, 0.0))
    pts.sort(key=lambda x: x[0])
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        if t0 <= t <= t1:
            return p0 + (p1 - p0) * ((t - t0).total_seconds() / max(1.0, (t1 - t0).total_seconds()))
    return 0.0


def detect_trips(samples: list[dict], faults: Optional[list[dict]], day: dt.date, tariff: float,
                 start: dt.time = dt.time(7, 0), end: dt.time = dt.time(17, 30)) -> list[dict[str, Any]]:
    """samples: readings of ONE day. faults: [{'t': datetime, 'active': [flag, ...]}, ...]."""
    s = sorted((x for x in samples if x["t"].date() == day), key=lambda x: x["t"])
    if not s:
        return []
    d0, d1 = dt.datetime.combine(day, start), dt.datetime.combine(day, end)
    a0, a1 = dt.datetime.combine(day, dt.time(7, 0)), dt.datetime.combine(day, dt.time(18, 0))
    runs, cur = [], []
    for i, x in enumerate(s):
        zero = d0 <= x["t"] <= d1 and x["p_kw"] <= STOP_KW
        gap = bool(cur) and (x["t"] - cur[-1][1]["t"]).total_seconds() > 900
        if zero and not gap:
            cur.append((i, x))
        else:
            if cur:
                runs.append(cur)
            cur = [(i, x)] if zero else []
    if cur:
        runs.append(cur)
    flagged = sorted((f for f in (faults or []) if f.get("active")), key=lambda f: f["t"])
    out = []
    for run in runs:
        i0, first = run[0]; i1, last = run[-1]
        before = s[i0 - 1] if i0 > 0 and s[i0 - 1]["p_kw"] > STOP_KW and (first["t"] - s[i0 - 1]["t"]).total_seconds() <= 900 else None
        after = s[i1 + 1] if i1 + 1 < len(s) and s[i1 + 1]["p_kw"] > STOP_KW and (s[i1 + 1]["t"] - last["t"]).total_seconds() <= 900 else None
        t_end = after["t"] if after else last["t"] + dt.timedelta(seconds=CADENCE)
        msgs = [f for f in flagged if first["t"] - dt.timedelta(minutes=15) <= f["t"] <= t_end + dt.timedelta(minutes=5)]
        minutes = (t_end - first["t"]).total_seconds() / 60
        if not msgs and not (before and after and minutes >= 10):
            continue                                  # sunrise, sunset or night: not a trip
        kind, flags = OTHER, []
        for f in msgs:
            for fl in f["active"]:
                if fl not in flags:
                    flags.append(fl)
        known = [fl for fl in flags if fl in FLAG_CODES]
        if known:
            kind = FLAG_CODES[known[0]][0]
        kwh = 0.0
        for k, (_, x) in enumerate(run):
            nxt = run[k + 1][1]["t"] if k + 1 < len(run) else t_end
            kwh += _expected(x["t"], before, after, a0, a1) * min(900.0, (nxt - x["t"]).total_seconds()) / 3600
        out.append({"start": first["t"], "end": t_end, "minutes": round(minutes, 1), "kind": kind,
                    "codes": [code_label(fl) for fl in flags], "flagged": bool(msgs),
                    "loss_kwh": round(kwh, 2), "loss_inr": round(kwh * tariff, 0)})
    return out


def summarize_trips(events: list[dict]) -> dict[str, dict[str, float]]:
    res = {k: {"n": 0, "minutes": 0.0, "kwh": 0.0, "inr": 0.0} for k in (GRID, INTERNAL, OTHER)}
    for e in events:
        r = res.get(e["kind"])
        if r is not None:
            r["n"] += 1; r["minutes"] += e.get("minutes") or 0; r["kwh"] += e.get("loss_kwh") or 0; r["inr"] += e.get("loss_inr") or 0
    return res