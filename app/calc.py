"""Small pure calculation helpers for the report (same rules as formulas.py: missing input => None => 'N/A')."""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Optional


def change(cur: Optional[float], prev: Optional[float]) -> Optional[tuple[float, float]]:
    """F09: (difference, percent) or None when there is no usable baseline."""
    if cur is None or prev is None or prev <= 0:
        return None
    return cur - prev, (cur - prev) / prev * 100


def days_sum(daily: dict[dt.date, float], year: int, month: int, n: int) -> Optional[float]:
    """Energy of days 1..n of a month; None unless EVERY one of those days is stored (never compare partial data)."""
    if n < 1 or n > calendar.monthrange(year, month)[1]:
        return None
    vals = [daily.get(dt.date(year, month, d)) for d in range(1, n + 1)]
    return None if any(v is None for v in vals) else float(sum(vals))


def month_info(daily: dict[dt.date, float], day: dt.date) -> dict:
    """This month so far (days present), and the same number of days in last month and in the same month last year."""
    mine = {d: v for d, v in daily.items() if (d.year, d.month) == (day.year, day.month) and d <= day}
    pm = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return {"kwh": sum(mine.values()), "days": len(mine), "elapsed": day.day,
            "last_month": days_sum(daily, pm[0], pm[1], day.day),
            "last_year": days_sum(daily, day.year - 1, day.month, day.day) if day.day <= calendar.monthrange(day.year - 1, day.month)[1] else None}


def monthly_totals(daily: dict[dt.date, float]) -> list[tuple[int, int, float, int]]:
    acc: dict[tuple[int, int], list[float]] = {}
    for d, v in daily.items():
        acc.setdefault((d.year, d.month), []).append(v)
    return [(y, m, sum(v), len(v)) for (y, m), v in sorted(acc.items())]


def perf_ratio(kwh: Optional[float], kwp: Optional[float], psh: Optional[float], days: float = 1) -> Optional[float]:
    """Benchmark performance ratio = (kWh / kWp) / (sun hours x days) x 100.  Not the IEC PR (needs a sun sensor)."""
    if kwh is None or not kwp or not psh or days <= 0:
        return None
    return kwh / (kwp * psh * days) * 100


def cuf(kwh: Optional[float], kwp: Optional[float], days: int) -> Optional[float]:
    """F07: CUF % = E / (P_DC x hours). Month: all calendar hours of the elapsed days."""
    if kwh is None or not kwp or days <= 0:
        return None
    return kwh / (kwp * 24 * days) * 100


def cuf_class(v: Optional[float]) -> str:
    """red below 16 %, orange 16-20 %, green above 20 %."""
    return "" if v is None else "red" if v < 16 else "orange" if v <= 20 else "green"