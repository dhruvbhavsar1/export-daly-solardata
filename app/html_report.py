"""Daily HTML report in the SolarPulse theme (self-contained, interactive chart).

Input : list of samples (see `parse_message`) for ONE IST calendar day.
Output: one HTML file. No external requests. Opens fine from email attachment/GitHub.

Rules kept from the project: zero is not missing; missing is never shown as zero;
unverified numbers carry a tag; money is called "Estimated value", never "savings".
"""
from __future__ import annotations

import datetime as dt
import html
import base64
import calendar
from app import calc, trips as tp
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

THEME_CSS = (Path(__file__).parent / "theme.css").read_text(encoding="utf-8")

# Device timestamp, read as UTC, already shows IST wall-clock (verified on 14 days of data:
# generation runs 06:30-18:40 on that clock). So IST time = utcfromtimestamp(ts).
IST_OFFSET_NOTE = "device clock = IST written as UTC"


@dataclass
class Site:
    client: str = "Client name"
    address: str = "—"
    plant_id: str = "—"
    serial: str = "—"
    inverter: str = "—"
    kwp: Optional[float] = None          # None => specific yield / performance show "N/A"
    tariff: float = 7.0                   # Rs per kWh (estimate)
    co2_kg_per_kwh: float = 0.82          # assumption, shown as such
    window_start: dt.time = dt.time(7, 0)
    window_end: dt.time = dt.time(18, 0)
    psh: Optional[float] = None           # peak sun hours/day (F03 expected = kWp x PSH); None => N/A
    tree_kg: float = 22.0                 # kg CO2 absorbed per tree per YEAR (assumption)
    inverter_make: str = ""
    inverter_model: str = ""
    inverter_sn: str = ""
    panel_type: str = ""
    plan: str = "Premium"                 # Basic / Standard / Premium
    invested_inr: Optional[float] = None
    commissioned: Optional[dt.date] = None
    helpline: str = "+91 7574-946000"      # shown in the page header
    city: str = "Surat"                   # weather location (all plants are in Surat; override per plant)
    lat: float = 21.17
    lon: float = 72.83


def parse_message(payload: dict) -> Optional[dict[str, Any]]:
    """functionId 2 = telemetry. Returns a flat sample in real units, or None."""
    pl = payload.get("payload", {})
    if pl.get("functionId") != 2:
        return None
    d = {x["name"]: x["value"] for x in pl.get("functionPayload", [])}
    if "activePower" not in d or "eToday" not in d:
        return None
    ist = dt.datetime.fromtimestamp(payload["timestamp"], dt.timezone.utc).replace(tzinfo=None)
    return {
        "mac": str(payload.get("headerMac") or "unknown"),
        "t": ist,
        "p_kw": d["activePower"] / 1000.0,
        "e_kwh": d["eToday"] / 1000.0,
        "total_kwh": d.get("totalEnergy"),
        "v": d.get("phaseVoltage1"),
        "i_a": (d["phaseCurrent1"] / 1000.0) if "phaseCurrent1" in d else None,
        "hz": d.get("gridFrequency"),
        "pf": (d["powerFactor"] / 100.0) if "powerFactor" in d else None,
        "mppt_v": d.get("mpptVoltage1"),
        "mppt_a": (d["mpptCurrent1"] / 1000.0) if "mpptCurrent1" in d else None,
        "status": d.get("inverterStatus"),
    }


def dedupe(samples: list[dict]) -> list[dict]:
    seen: dict[dt.datetime, dict] = {}
    for s in samples:
        seen[s["t"]] = s
    return [seen[k] for k in sorted(seen)]


def summarize(samples: list[dict], site: Site) -> dict[str, Any]:
    s = dedupe(samples)
    out: dict[str, Any] = {"n": len(s)}
    if not s:
        return out
    energy = max(x["e_kwh"] for x in s)  # daily counter, full day (not only the window)
    peak = max(s, key=lambda x: x["p_kw"])
    # cross-check: integrate power (gaps > 15 min are not bridged)
    integ = 0.0
    for a, b in zip(s, s[1:]):
        g = (b["t"] - a["t"]).total_seconds()
        if g <= 900:
            integ += (a["p_kw"] + b["p_kw"]) / 2 * g / 3600
    gen = [x for x in s if x["p_kw"] > 0.05]
    gaps = sum(1 for a, b in zip(s, s[1:]) if (b["t"] - a["t"]).total_seconds() > 600)
    out.update(
        energy=energy,
        peak_kw=peak["p_kw"], peak_t=peak["t"],
        integ=integ,
        mismatch_pct=(abs(integ - energy) / energy * 100) if energy > 0 else None,
        first=gen[0]["t"] if gen else None, last=gen[-1]["t"] if gen else None,
        value_inr=energy * site.tariff, co2=energy * site.co2_kg_per_kwh,
        yield_kwh_kwp=(energy / site.kwp) if site.kwp else None,
        gaps=gaps,
        v_min=min((x["v"] for x in s if x["v"] and x["p_kw"] > 0.5), default=None),
        v_max=max((x["v"] for x in s if x["v"] and x["p_kw"] > 0.5), default=None),
        hz_set=sorted({x["hz"] for x in s if x["hz"] and x["p_kw"] > 0.5}),
        i_max=max((x["i_a"] for x in s if x["i_a"] is not None), default=None),
        mppt_v_max=max((x["mppt_v"] for x in s if x["mppt_v"]), default=None),
        total_kwh=s[-1]["total_kwh"],
    )
    return out


def _f(x: Optional[float], nd: int = 1, unit: str = "") -> str:
    return "N/A" if x is None else f"{x:,.{nd}f}{unit}"


def _delta(cur: float, prev: Optional[float]) -> str:
    if prev is None or prev <= 0:
        return '<b class="dim">no previous day stored</b>'
    d = cur - prev
    col = "var(--green)" if d >= 0 else "var(--pink)"
    return f'<b style="color:{col}">{d:+.1f} kWh ({d / prev * 100:+.1f}%)</b>'


def _chart(samples: list[dict], day: dt.date, site: Site) -> str:
    """Inline SVG power curve + hover tooltip (tiny JS, works in a browser, ignored by email)."""
    w, h, ml, mr, mt, mb = 900, 300, 52, 16, 14, 34
    t0 = dt.datetime.combine(day, dt.time(6, 0))
    t1 = dt.datetime.combine(day, dt.time(19, 0))
    span = (t1 - t0).total_seconds()
    pmax = max([x["p_kw"] for x in samples] + [1.0])
    ymax = (int(pmax * 1.15) + 1)

    def X(t): return ml + (t - t0).total_seconds() / span * (w - ml - mr)
    def Y(p): return mt + (1 - p / ymax) * (h - mt - mb)

    pts = [x for x in samples if t0 <= x["t"] <= t1]
    # break the line where data is missing (> 15 min), never draw through a gap as zero
    segs, cur = [], []
    for x in pts:
        if cur and (x["t"] - cur[-1]["t"]).total_seconds() > 900:
            segs.append(cur); cur = []
        cur.append(x)
    if cur: segs.append(cur)
    area = "".join(
        f'<path d="M{X(sg[0]["t"]):.1f},{Y(0):.1f} ' + " ".join(f'L{X(p["t"]):.1f},{Y(p["p_kw"]):.1f}' for p in sg)
        + f' L{X(sg[-1]["t"]):.1f},{Y(0):.1f}Z" fill="#E5A000" fill-opacity=".18"/>' for sg in segs)
    line = "".join(
        '<polyline fill="none" stroke="#C88D00" stroke-width="2.2" points="'
        + " ".join(f'{X(p["t"]):.1f},{Y(p["p_kw"]):.1f}' for p in sg) + '"/>' for sg in segs)
    grid = "".join(
        f'<line x1="{ml}" x2="{w-mr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="#E3E9E4"/>'
        f'<text x="{ml-8}" y="{Y(v)+4:.1f}" text-anchor="end" font-size="11" fill="#78889c">{v} kW</text>'
        for v in range(0, ymax + 1))
    xt = "".join(
        f'<text x="{X(dt.datetime.combine(day, dt.time(hh))):.1f}" y="{h-12}" text-anchor="middle" font-size="11" fill="#78889c">{hh:02d}:00</text>'
        for hh in range(6, 20, 2))
    ws, we = (dt.datetime.combine(day, site.window_start), dt.datetime.combine(day, site.window_end))
    band = (f'<rect x="{X(ws):.1f}" y="{mt}" width="{X(we)-X(ws):.1f}" height="{h-mt-mb}" fill="#128260" fill-opacity=".05"/>')
    data = json.dumps([[x["t"].strftime("%H:%M"), round(x["p_kw"], 3), round(X(x["t"]), 1), round(Y(x["p_kw"]), 1)] for x in pts])
    return f"""
<div class="chartbox"><h3>Power curve · {day:%d %b %Y} (kW, every ~5 min)</h3>
<svg id="pc" viewBox="0 0 {w} {h}" role="img" aria-label="Active power over the day">
{band}{grid}{xt}{area}{line}
<line id="cur" x1="0" x2="0" y1="{mt}" y2="{h-mb}" stroke="#18372F" stroke-dasharray="3 3" visibility="hidden"/>
<circle id="dot" r="5" fill="#128260" stroke="#fff" stroke-width="2" visibility="hidden"/>
<text id="tip" font-size="12" font-weight="700" fill="#18372F" visibility="hidden"></text>
</svg>
<div class="chartlegend"><span><i class="key" style="background:#C88D00"></i>Active power</span>
<span><i class="key expected" style="background:#DCEFE3"></i>Report window {site.window_start:%H:%M}–{site.window_end:%H:%M}</span></div>
<div class="chart-note">Gaps in the line mean no data was received, not zero output. Hover (or touch) the chart to read values.</div></div>
<script>(function(){{var D={data},s=document.getElementById('pc');if(!s)return;
var c=s.querySelector('#cur'),d=s.querySelector('#dot'),t=s.querySelector('#tip');
function mv(e){{var r=s.getBoundingClientRect(),cl=e.touches?e.touches[0].clientX:e.clientX,
x=(cl-r.left)/r.width*{w},b=null,m=1e9;D.forEach(function(p){{var q=Math.abs(p[2]-x);if(q<m){{m=q;b=p}}}});
if(!b)return;c.setAttribute('x1',b[2]);c.setAttribute('x2',b[2]);d.setAttribute('cx',b[2]);d.setAttribute('cy',b[3]);
t.textContent=b[0]+'  '+b[1].toFixed(2)+' kW';t.setAttribute('x',Math.min(b[2]+10,{w-150}));t.setAttribute('y',{mt+14});
[c,d,t].forEach(function(n){{n.setAttribute('visibility','visible')}})}}
s.addEventListener('mousemove',mv);s.addEventListener('touchmove',mv,{{passive:true}});
s.addEventListener('mouseleave',function(){{[c,d,t].forEach(function(n){{n.setAttribute('visibility','hidden')}})}})}})();</script>"""


LOGO = ""
for _n in ("logo_small.png", "logo_solserva.png"):
    _lp = Path(__file__).parent / _n
    if _lp.exists():
        LOGO = "data:image/png;base64," + base64.b64encode(_lp.read_bytes()).decode()
        break


def parse_faults(payload: dict) -> Optional[dict[str, Any]]:
    """functionId 15 = fault flags. Returns the time and the names of flags that are set."""
    pl = payload.get("payload", {})
    if pl.get("functionId") != 15:
        return None
    t = dt.datetime.fromtimestamp(payload["timestamp"], dt.timezone.utc).replace(tzinfo=None)
    return {"t": t, "mac": str(payload.get("headerMac") or ""),
            "active": sorted(x["name"] for x in pl.get("functionPayload", []) if x["value"] and x["name"] != "deviceType")}


def _inr(v: Optional[float]) -> str:
    """Indian digit grouping without decimals: 1000000 -> 10,00,000."""
    n = int(round(abs(v or 0)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        s = head + "," + tail
    return ("-" if (v or 0) < 0 else "") + s


def _slots(s: list[dict]) -> list[tuple[str, float]]:
    """Today's energy split by time of day, from differences of the inverter's daily counter."""
    firsts: dict[int, dict] = {}
    for x in s:
        firsts.setdefault(x["t"].hour, x)
    hrs = sorted(firsts)[1:]
    pts = [s[0]["e_kwh"]] + [firsts[h]["e_kwh"] for h in hrs] + [s[-1]["e_kwh"]]
    labs, prev = [f"Before {s[0]['t']:%H:%M}"], f"{s[0]['t']:%H:%M}"
    for h in hrs:
        labs.append(f"{prev}–{h:02d}:00"); prev = f"{h:02d}:00"
    labs.append(f"{prev}–{s[-1]['t']:%H:%M}")
    return list(zip(labs, [pts[0]] + [max(0.0, pts[i + 1] - pts[i]) for i in range(len(pts) - 1)]))


def _lines(labels: list[str], actual: list, expected: Optional[list]) -> str:
    """Actual (blue) vs expected (grey dashed) line chart; None values leave a gap."""
    w, h, ml, mb = 900, 300, 52, 36
    vals = [v for v in actual + (expected or []) if v is not None]
    top = max(vals) if vals else 1.0
    step = 10 ** max(0, len(str(int(top))) - 1) / 2 or 1
    ym = max(step, -(-top // step) * step)
    X = lambda i: ml + (i + 0.5) / max(1, len(labels)) * (w - ml - 16)
    Y = lambda v: 14 + (1 - v / ym) * (h - 14 - mb)
    g = "".join(f'<line x1="{ml}" x2="{w-16}" y1="{Y(v):.0f}" y2="{Y(v):.0f}" stroke="#E3E9E4"/><text x="{ml-8}" y="{Y(v)+4:.0f}" text-anchor="end" font-size="11" fill="#78889c">{v:g}</text>' for v in [ym * i / 4 for i in range(5)])
    every = max(1, len(labels) // 10)
    g += "".join(f'<text x="{X(i):.0f}" y="{h-12}" text-anchor="middle" font-size="11" fill="#78889c">{lb}</text>' for i, lb in enumerate(labels) if i % every == 0)

    def path(series, extra):
        segs, cur = [], []
        for i, v in enumerate(series):
            if v is None:
                if cur: segs.append(cur); cur = []
            else:
                cur.append(f"{X(i):.1f},{Y(v):.1f}")
        if cur: segs.append(cur)
        return "".join(f'<polyline points="{" ".join(sg)}" fill="none" {extra}/>' for sg in segs)
    body = (path(expected, 'stroke="#C9CED6" stroke-width="2" stroke-dasharray="6 5"') if expected else "") + path(actual, 'stroke="#2B75C7" stroke-width="2.5"')
    body += "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.5" fill="#2B75C7"><title>{labels[i]}: {v:.1f} kWh</title></circle>' for i, v in enumerate(actual) if v is not None)
    return f'<svg viewBox="0 0 {w} {h}" role="img">{g}{body}</svg>'


def _bars(labels: list[str], values: list, note: str = "") -> str:
    mx = max([v for v in values if v] or [1])
    return '<div class="bars">' + "".join(
        f'<div class="baritem"><div class="bar" title="{lb}: {(v or 0):.1f} kWh" style="height:{max(3, (v or 0) / mx * 85):.0f}%;{"opacity:.15" if v is None else ""}"></div><small>{lb}</small></div>'
        for lb, v in zip(labels, values)) + "</div>" + (f'<p class="note">{note}</p>' if note else "")


EXTRA_CSS = """
.brand .n1{font:900 31px/1 'Arial Black',Arial,sans-serif;color:#0B2A6B;letter-spacing:-.5px}
.brand .n2{font:900 18px/1.2 'Arial Black',Arial,sans-serif;color:#1B6B1E;margin-top:2px}
.terminal .tg{display:grid;grid-template-columns:1fr 1fr;gap:0 40px}.terminal .termrow{max-width:none}
.chg{font-size:12px;margin-top:10px;color:#65796E}.chg b.up{color:#0E8660}.chg b.dn{color:#C0392B}
.plansel{display:flex;gap:10px;flex-wrap:wrap}.plansel div{flex:1;min-width:110px;text-align:center;padding:12px;border:1px solid #CFE2D6;border-radius:10px;color:#65796E;background:#F2F7F3}
.plansel .on{background:#128260;color:#fff;border-color:#128260;font-weight:800}
.red{color:#C0392B!important}.orange{color:#D97A00!important}.green{color:#0E8660!important}
.seg{display:flex;gap:8px;margin:14px 0 4px}.seg button{cursor:pointer;background:#EDF5EF;border:1px solid #CFDFD3;color:#225642;padding:9px 16px;border-radius:8px;font:inherit}
.seg button.on{background:#128260;color:#fff;border-color:#128260}.vw{display:none}.vw.on{display:block}
.quad{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px}@media(max-width:900px){.quad{grid-template-columns:1fr 1fr}}
.chartbox .key{background:#2B75C7}.tripcards{display:grid;grid-template-columns:1fr 1fr;gap:18px}
table.t{width:100%;border-collapse:collapse;font-size:13px}table.t td,table.t th{padding:8px 10px;border-bottom:1px solid var(--border);text-align:left}
@media(max-width:650px){.tripcards{grid-template-columns:1fr}.terminal .tg{grid-template-columns:1fr}}
@media print{.vw{display:block!important}.seg{display:none}.panel,.card,.terminal,.savings-feature,.notice{break-inside:avoid}.section-title{break-after:avoid}}
"""


def build_daily_html(samples: list[dict], day: dt.date, site: Site,
                     prev_energy: Optional[float] = None, month_energy: Optional[float] = None,
                     generated_at: Optional[dt.datetime] = None, month_days: Optional[list] = None,
                     faults: Optional[list] = None, history: Optional[dict] = None,
                     trips: Optional[list] = None, month_trips: Optional[list] = None,
                     weather: Optional[dict] = None) -> str:
    """history: {date: kWh} of ALL stored days (today may be missing). faults: fault messages of the day
    [{'t','active'}] or None if none arrived. trips: events of today (detected here if None).
    month_trips: stored trip events of the EARLIER days of this month."""
    s = dedupe([x for x in samples if x["t"].date() == day])
    m = summarize(s, site)
    esc = html.escape
    gen_dt = generated_at or (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(hours=5, minutes=30))
    gen_at = gen_dt.strftime("%d %b %Y, %H:%M IST")
    if not s:
        return _page(site, day, gen_at, '<div class="notice">No data was received for this day. Nothing is shown as zero.</div>', "NO DATA")
    NA = '<span class="dim">N/A</span>'
    row = lambda a, b: f'<div class="row"><span>{a}</span><b>{b}</b></div>'
    e, kw, kwp = m["energy"], [x["p_kw"] for x in s], site.kwp
    hist = dict(history or {})
    if history is None:
        if prev_energy is not None: hist[day - dt.timedelta(days=1)] = prev_energy
        hist.update(dict(month_days or []))
    hist[day] = e
    mi = calc.month_info(hist, day)
    mon = mi["kwh"] if (history is not None or month_days) else (month_energy if month_energy is not None else e)
    yest = hist.get(day - dt.timedelta(days=1))
    peak_day = max(v for d, v in hist.items() if (d.year, d.month) == (day.year, day.month)) if hist else None
    n_exp = int((dt.datetime.combine(day, site.window_end) - dt.datetime.combine(day, site.window_start)).total_seconds() / 300)
    in_win = [x for x in s if site.window_start <= x["t"].time() <= site.window_end]
    cover = len(in_win) / n_exp * 100
    mm = m["mismatch_pct"]; scale_ok = mm is not None and mm <= 10
    # ---- derived values
    psh = site.psh
    pr_day, pr_mon = calc.perf_ratio(e, kwp, psh), calc.perf_ratio(mon, kwp, psh, mi["days"])
    cuf_m = calc.cuf(mon, kwp, mi["elapsed"]); cuf_c = calc.cuf_class(cuf_m)
    exp_mon = kwp * psh * mi["days"] if (kwp and psh) else None
    yld_m = mon / kwp if kwp else None
    f = site.co2_kg_per_kwh
    co2_d, co2_m = e * f, mon * f
    tr_d, tr_m = co2_d / site.tree_kg, co2_m / site.tree_kg
    val_d, val_m = e * site.tariff, mon * site.tariff
    # ---- trips
    ev = trips if trips is not None else tp.detect_trips(s, faults, day, site.tariff)
    sd, sm = tp.summarize_trips(ev), tp.summarize_trips(list(month_trips or []) + ev)
    # ---- communication / health
    last_t = s[-1]["t"]
    ref = min(gen_dt, dt.datetime.combine(day, site.window_end))
    online = (ref - last_t).total_seconds() <= 900
    active_flags = sorted({fl for fa in (faults or []) for fl in fa["active"]})
    v_ok = m["v_min"] is not None and 195 <= m["v_min"] and m["v_max"] <= 265 and all(48.5 <= h_ <= 51.5 for h_ in m["hz_set"]) and bool(m["hz_set"])
    checks = [online, not ev and not active_flags, v_ok, cover >= 90]
    health = 25 * sum(checks)
    # ---- ROI
    life = m["total_kwh"]
    recovered = life * site.tariff if life is not None else None
    inv = site.invested_inr
    roi = recovered / inv * 100 if (inv and recovered is not None) else None
    yrs = (day - site.commissioned).days / 365.25 if site.commissioned else None
    annual = recovered / yrs if (recovered is not None and yrs and yrs > 0.1) else None
    payback = inv / annual if (inv and annual) else None
    ach_m = pr_mon                                            # Actual vs Expected (month): benchmark factor 1.0
    money = lambda x: NA if x is None else f"₹{_inr(x)}"

    def chg(label, cur, prev, basis=""):
        c = calc.change(cur, prev)
        if c is None:
            return f'<div class="chg">{label}: <b class="dim">N/A</b></div>'
        return f'<div class="chg">{label}: <b class="{"up" if c[0] >= 0 else "dn"}">{c[0]:+.1f} kWh ({c[1]:+.1f}%)</b>{basis}</div>'
    chg_row = lambda label, cur, prev: row(label, NA if calc.change(cur, prev) is None else f'<span class="{"green" if cur >= prev else "red"}">{cur - prev:+.1f} kWh ({(cur - prev) / prev * 100:+.1f}%)</span>')

    hero = f"""
<section class="savings-feature"><div class="savings-story">
<h2>Your Solar<br/>Saved <em>₹{_inr(val_d)}</em><br/>Today</h2>
<p>{esc(site.client)}'s plant produced {e:.1f} kWh on {day:%A, %d %B %Y}, peaking at {m['peak_kw']:.2f} kW around {m['peak_t']:%H:%M}. The figures are estimates at ₹{site.tariff:g} per kWh, not a bill amount.</p></div>
<div class="savings-metrics">
<div class="savings-tile yellow"><strong>₹{_inr(val_d)}</strong><span>ESTIMATED VALUE</span></div>
<div class="savings-tile black"><strong>₹{_inr(val_m)}</strong><span>ESTIMATED VALUE THIS MONTH</span></div>
<div class="savings-tile stone"><strong>{e:.1f}</strong><span>KWH TODAY</span></div>
<div class="savings-tile white"><strong>{m['peak_kw']:.2f}</strong><span>PEAK KW</span></div></div></section>"""

    card = lambda c, t, v, u, n: f'<div class="card" style="--accent:var(--{c})"><small>{t}</small><div class="value">{v} <span style="font-size:16px">{u}</span></div><div class="note">{n}</div></div>'
    nm = f"Month-to-date vs same {mi['elapsed']} days of last month"
    kpis = '<section class="grid kpis" style="grid-template-columns:repeat(6,minmax(0,1fr))">' + "".join([
        card("sun", "Today’s Generation", f"{e:.1f}", "kWh", chg("+ / − From Yesterday", e, yest)),
        card("cyan", "This Month (stored days)", f"{mon:.1f}", "kWh", chg("+ / − From Last Month", mon, mi["last_month"])),
        card("green", "Lifetime Generation", _f(life, 0), "kWh", "Inverter counter"),
        card("pink", "Performance Ratio", _f(pr_day, 1), "%" if pr_day is not None else "", f"Today, assumed {psh:g} sun hours" if (psh and kwp) else "Needs plant kWp and sun hours"),
        card("pink", "Specific Yield", _f(e / kwp if kwp else None, 2), "kWh/kWp" if kwp else "", "Today = kWh ÷ kWp" if kwp else "Plant kWp not set"),
        card("green", "CO₂ Avoided (estimate)", f"{co2_d:.1f}", "kg", f"≈ {tr_d:.2f} tree-years ({co2_d:.1f} ÷ {site.tree_kg:g})")]) + "</section>"

    mk, md, sn = site.inverter_make or "", site.inverter_model or "", site.inverter_sn or ""
    if not (mk or md) and site.inverter not in ("", "—", "-"):
        md = site.inverter
    fld = lambda a, b: f'<div class="field"><span>{a}</span><b>{esc(b) if b and b not in ("-", "—") else "—"}</b></div>'
    plans = "".join(f'<div class="{"on" if p.lower() == (site.plan or "").lower() else ""}">{p}</div>' for p in ("Basic", "Standard", "Premium"))
    plant = f"""
<h2 class="section-title">01 / Plant information</h2><section class="panel"><div class="fieldgrid">
{fld("Name of Client", site.client)}{fld("Address of Plant", site.address)}{fld("Plant ID", site.plant_id)}
<div class="field"><span>Capacity of Plant (DC)</span><b>{'N/A' if kwp is None else f'{kwp:g} kWp'}</b></div>{fld("Panel Type", site.panel_type)}{fld("Data Logger", site.serial)}
{fld("Inverter Make", mk)}{fld("Inverter Model", md)}{fld("Inverter S. No.", sn)}</div>
<div class="field" style="margin-top:12px"><span>Service Code (Subscription Plan)</span><div class="plansel">{plans}</div></div></section>"""

    cmp_ = (row("Today’s Generation", f"{e:.1f} kWh") + chg_row("+ / − From Yesterday", e, yest) + row("Yesterday", _f(yest, 1, " kWh"))
            + row("Peak Day (this month)", _f(peak_day, 1, " kWh")) + row("This Month so far", f"{mon:.1f} kWh")
            + chg_row("+ / − From Last Month", mon, mi["last_month"]) + row("Last Month (same days)", _f(mi["last_month"], 1, " kWh"))
            + row("Same Month Last Year (same days)", _f(mi["last_year"], 1, " kWh")) + chg_row("+ / − From Last Year Same Month", mon, mi["last_year"])
            + row("Lifetime energy", _f(life, 0, " kWh")))
    tr = lambda k, v: f'<div class="termrow"><span>{k}</span><span>{v}</span></div>'
    gen = f"""
<h2 class="section-title">02 / Generation data</h2>
<section class="cols"><div class="terminal"><div class="terminaltitle">&gt; GENERATION_DATA</div>
<div class="big">{e:.1f}</div><div class="unit">kWh today</div>
<div class="tg"><div>{tr("TODAY.......", f"{e:.1f} kWh")}{tr("YESTERDAY...", _f(yest, 1, " kWh"))}{tr("PEAK.........", f"{m['peak_kw']:.2f} kW")}</div>
<div>{tr("MONTH SO FAR", f"{mon:.1f} kWh")}{tr("FIRST LIGHT..", f"{m['first']:%H:%M}")}{tr("LAST LIGHT...", f"{m['last']:%H:%M}")}{tr("LIFETIME.....", _f(life, 0, " kWh"))}</div></div></div>
<div class="panel"><h2>Daily &amp; monthly comparison</h2><div class="caption">Comparisons use the same number of days; N/A until those days are stored</div>{cmp_}</div></section>
<section class="panel" style="margin-top:18px">{_chart(s, day, site)}</section>"""

    sl = _slots(s)
    cols_ = ["var(--sun)", "var(--green)", "var(--cyan)", "var(--pink)", "var(--blue)"]
    acc, stops = 0.0, []
    for i, (_, v) in enumerate(sl):
        p = v / e * 100 if e else 0
        stops.append(f"{cols_[i % 5]} {acc:.2f}% {acc + p:.2f}%"); acc += p
    leg = "".join(f'<div class="legendrow"><span><i class="dot" style="background:{cols_[i % 5]}"></i>{a}</span><b>{v:.1f} kWh</b></div>' for i, (a, v) in enumerate(sl))
    mt = lambda t, v, c, w: f'<div class="meter"><div class="meterhead"><span>{t}</span><b style="color:var({c})">{v}</b></div><div class="track"><div class="fill" style="width:{max(0, min(100, w)):.1f}%;background:var({c})"></div></div></div>'
    ov = f"""
<section class="cols"><div class="panel"><h2>Graphical View-01 • Generation overview</h2><div class="caption">Today’s energy by time of day (inverter counter)</div>
<div class="stats"><div class="donut" style="background:conic-gradient({','.join(stops)})"><div class="donutinner"><strong>{e:.1f}</strong><span>kWh today</span></div></div>
<div>{leg}<div class="legendrow"><span><i class="dot" style="background:#18372F"></i>Total generated today</span><b>{e:.1f} kWh</b></div></div></div>
<p class="note">Export to grid and self-consumed energy need a meter, so they are not shown.</p></div>
<div class="panel"><h2>Performance Meters</h2><div class="caption">Performance ratio • Investment ROI • System health</div>
{mt("Actual vs Expected (month)", _f(ach_m, 1, "%") if ach_m is not None else "N/A", "--sun", ach_m or 0)}
{mt("ROI Progress", _f(roi, 1, "%") if roi is not None else "N/A", "--green", roi or 0)}
{mt("System Health", f"{health}%", "--cyan", health)}</div></section>"""

    # ---- 03: month-wise / day-wise
    dim = calendar.monthrange(day.year, day.month)[1]
    dl = [str(d) for d in range(1, dim + 1)]
    da = [hist.get(dt.date(day.year, day.month, d)) if d <= day.day else None for d in range(1, dim + 1)]
    de = [kwp * psh if (kwp and psh and d <= day.day) else None for d in range(1, dim + 1)]
    mts = calc.monthly_totals(hist)
    ml_ = [dt.date(y, mo, 1).strftime("%b %y") for y, mo, _, _ in mts]
    ma = [t for _, _, t, _ in mts]
    me = [kwp * psh * n if (kwp and psh) else None for _, _, _, n in mts]
    has_exp = lambda xs: xs if any(v is not None for v in xs) else None
    sw = "<script>function sv(k,b){document.querySelectorAll('.vw').forEach(function(n){n.classList.toggle('on',n.dataset.v===k)});document.querySelectorAll('.seg button').forEach(function(n){n.classList.toggle('on',n===b)})}</script>"
    g3 = f"""
<h2 class="section-title">03 / All calculations &amp; graphical views</h2>
<div class="seg"><button class="on" onclick="sv('d',this)">Day-wise</button><button onclick="sv('m',this)">Month-wise</button></div>
<section class="cols"><div class="panel"><div class="chartbox"><h3>All Calculations — Actual vs Expected (kWh)</h3>
<div class="vw on" data-v="d">{_lines(dl, da, has_exp(de))}</div><div class="vw" data-v="m">{_lines(ml_, ma, has_exp(me))}</div>
<div class="chartlegend"><span><i class="key"></i>Actual kWh</span><span><i class="key expected"></i>Expected kWh</span></div>
<div class="chart-note">Expected = kWp × sun hours × days. It is drawn only when kWp and sun hours are set. Days or months without stored data are left empty.</div></div></div>
<div class="panel"><h2>Graphical View-03 • Generation</h2><div class="caption">Energy per day of {day:%B %Y}, or per month</div>
<div class="vw on" data-v="d">{_bars(dl, da)}</div><div class="vw" data-v="m">{_bars(ml_, ma, "Months with partial data show only the stored days.")}</div>
{row("Unit Generation (kWh / kWp / Month)", _f(yld_m, 2))}{row("Same Unit Generation Today", _f(e / kwp if kwp else None, 2, " kWh/kWp"))}</div></section>
<section class="cols equal"><div class="panel"><h2>All calculation</h2><div class="caption">Calculated from logger data (month so far, {mi['days']} stored day{'s' if mi['days'] != 1 else ''})</div>
{row("Expected kWh (kWp × sun hours × days)", _f(exp_mon, 1, " kWh"))}{row("Actual kWh", f"{mon:.1f} kWh")}{row("Performance Ratio", _f(pr_mon, 1, "%"))}{row("Specific Yield (F06)", _f(yld_m, 2, " kWh/kWp"))}
{row("CUF (Month)", f'<span class="{cuf_c}">{_f(cuf_m, 1, "%")}</span>')}{row(f"CO₂ Offset (est., {f:g} kg/kWh)", f"{co2_m:.1f} kg")}{row("Estimated value", money(val_m))}</div>
<div class="panel"><h2>Financial Data</h2><div class="caption">Estimates at the tariff below</div>
{row("Estimated value this month", money(val_m))}{row("Tariff", f"₹{site.tariff:.2f} / Unit")}{row("Annual Saving (estimate)", money(annual))}{row("Payback", _f(payback, 1, " years"))}{row("Actual Saving", "— (meter needed)")}{row("Date &amp; Time of Report Generation", gen_at)}</div></section>{sw}"""

    if weather:
        w = weather
        wx = (f'<div class="quad"><div class="mini"><small>Temperature</small><strong>{w["tmax"]:.0f}° / {w["tmin"]:.0f}°C</strong><small>max / min</small></div>'
              f'<div class="mini"><small>Humidity</small><strong>{_f(w["humidity"], 0, "%")}</strong><small>average in daylight</small></div>'
              f'<div class="mini"><small>Sunrise</small><strong>{w["sunrise"]:%H:%M}</strong><small>IST</small></div>'
              f'<div class="mini"><small>Sunset</small><strong>{w["sunset"]:%H:%M}</strong><small>{w["daylight_h"]:.1f} h of daylight</small></div></div>')
    else:
        wx = '<p class="note">Weather not available for this report.</p>'
    wsec = (f'<section class="panel" style="margin-top:18px"><h2>Weather &amp; sun — {esc(site.city)}</h2><div class="caption">Basic conditions for {day:%d %b %Y}</div>{wx}'
            '<p class="note">Weather data by <a href="https://open-meteo.com/" style="color:inherit">Open-Meteo.com</a> (CC BY 4.0).</p></section>')

    comm = f'<b class="{"" if online else "warn"}">{"ONLINE" if online else "OFFLINE since " + format(last_t, "%H:%M")}</b>'
    fl = "No fault message received" if faults is None else ("None active" if not active_flags else "Active: " + ", ".join(tp.code_label(x) for x in active_flags))
    sysh = f"""
<h2 class="section-title">04 / System, environment &amp; investment</h2>
<section class="cols equal"><div class="panel"><h2>System Health</h2><div class="caption">Health score {health}% = communication, no trip or fault, grid in range, 90% of readings received</div><div class="badges">
<div class="status"><span>Communication</span>{comm}</div>
<div class="status"><span>Grid voltage (range)</span><b>{_f(m['v_min'], 0)}–{_f(m['v_max'], 0)} V</b></div>
<div class="status"><span>Grid frequency</span><b>{' / '.join(str(x) for x in m['hz_set']) or 'N/A'} Hz</b></div>
<div class="status"><span>Max AC current</span><b>{_f(m['i_max'], 1, ' A')}</b></div>
<div class="status"><span>Max DC voltage (MPPT 1)</span><b>{_f(m['mppt_v_max'], 0, ' V')}</b></div>
<div class="status"><span>Inverter temperature</span><b class="warn">Not reported</b></div>
<div class="status"><span>Fault flags</span><b class="{'warn' if active_flags else ''}">{esc(fl)}</b></div></div></div>
<div class="panel"><h2>CO₂ Saved</h2><div class="caption">Trees = Annual CO₂ (kg) ÷ {site.tree_kg:g} kg per tree per year</div>
<div class="dual"><div class="mini"><small>CO₂ avoided — today</small><strong>{co2_d:.1f} kg</strong></div><div class="mini"><small>Tree-years — today</small><strong>{tr_d:.2f}</strong></div>
<div class="mini"><small>CO₂ avoided — this month</small><strong style="color:var(--green)">{co2_m:.1f} kg</strong></div><div class="mini"><small>Tree-years — this month</small><strong>{tr_m:.2f}</strong></div></div>
{row("Equivalent trees (needs a year of data)", NA)}{row("Check: 6,000 ÷ 22", "≈ 273 trees ✓")}</div></section>
{wsec}
<section class="panel" style="margin-top:18px"><h2>Investment ROI</h2><div class="caption">From the plant data in plants.json and the inverter’s lifetime energy (estimate)</div>
<div class="dual"><div class="mini"><small>Investment ROI</small><strong style="color:var(--sun)">{_f(roi, 1, "%")}</strong></div><div class="mini"><small>Payback</small><strong>{_f(payback, 1, " years")}</strong></div>
<div class="mini"><small>Recovered</small><strong>{money(recovered) if inv else 'N/A'}</strong></div><div class="mini"><small>Invested</small><strong>{money(inv)}</strong></div></div>
{mt("ROI Progress", _f(roi, 1, "%") if roi is not None else "N/A", "--sun", roi or 0)}</section>"""

    def tcard(kind):
        a, b = sd[kind], sm[kind]
        return (f'<div class="panel"><h2>{esc(kind)}</h2><div class="caption">Estimated loss while the inverter was stopped</div>'
                + row("Trips today / this month", f"{a['n']} / {b['n']}") + row("Time stopped today", f"{a['minutes']:.0f} min")
                + row("Estimated loss today", f"{a['kwh']:.1f} kWh · ₹{_inr(a['inr'])}") + row("Estimated loss this month", f"{b['kwh']:.1f} kWh · ₹{_inr(b['inr'])}") + "</div>")
    trs = "".join(f'<tr><td>{x["start"]:%H:%M}–{x["end"]:%H:%M}</td><td>{x["minutes"]:.0f} min</td><td>{esc(x["kind"])}</td><td>{esc(", ".join(x["codes"]) or "no alarm received")}</td><td>{x["loss_kwh"]:.1f} kWh</td><td>₹{_inr(x["loss_inr"])}</td></tr>' for x in ev)
    tsec = f"""
<h2 class="section-title">05 / Trips &amp; estimated loss</h2>
<section class="tripcards">{tcard(tp.GRID)}{tcard(tp.INTERNAL)}</section>
<section class="panel" style="margin-top:18px"><h2>Trip log — {day:%d %b %Y}</h2><div class="caption">A trip is a daytime stop (07:00–17:30). Loss is an estimate: a straight line between the readings before and after the stop.</div>
{('<div style="overflow-x:auto"><table class="t"><thead><tr><th>Time</th><th>Stopped</th><th>Type</th><th>Alarm code</th><th>Est. loss</th><th>Est. value</th></tr></thead><tbody>' + trs + '</tbody></table></div>') if ev else '<p class="note">No trip detected today.</p>'}
{row("Unclassified stops this month (no alarm received, not counted above)", f"{sm[tp.OTHER]['n']} · {sm[tp.OTHER]['kwh']:.1f} kWh") if sm[tp.OTHER]['n'] else ''}</section>"""

    rows = "".join(
        f'<tr><td>{x["t"]:%H:%M}</td><td>{x["p_kw"]:.3f}</td><td>{x["e_kwh"]:.2f}</td><td>{_f(x["v"], 0)}</td><td>{_f(x["i_a"], 2)}</td><td>{_f(x["mppt_v"], 0)}</td><td>{_f(x["mppt_a"], 2)}</td></tr>' for x in in_win)
    table = f"""
<h2 class="section-title">06 / Readings</h2><section class="panel"><details><summary style="cursor:pointer;font-weight:700">Show {len(in_win)} readings ({site.window_start:%H:%M}–{site.window_end:%H:%M})</summary>
<div style="overflow-x:auto;margin-top:14px"><table class="t"><thead><tr><th>Time</th><th>Power kW</th><th>Today kWh</th><th>AC V</th><th>AC A</th><th>DC V</th><th>DC A</th></tr></thead>
<tbody>{rows}</tbody></table></div></details></section>"""
    ratio = m["peak_kw"] / kwp if kwp else None
    cap = row("Peak power vs plant capacity", NA if ratio is None else f'<span style="color:{"var(--sun)" if (ratio < 0.15 or ratio > 1.1) else "var(--green)"}">{m["peak_kw"]:.1f} kW of {kwp:g} kWp ({ratio * 100:.0f}%)</span>')
    quality = f"""
<h2 class="section-title">07 / Data quality</h2><section class="panel">
{row(f"Readings received in {site.window_start:%H:%M}–{site.window_end:%H:%M}", f"{len(in_win)} of ~{n_exp} ({cover:.0f}%)")}
{row("Gaps longer than 10 min", m["gaps"])}
{row("Energy cross-check (power × time vs counter)", f'<span style="color:{"var(--green)" if scale_ok else "var(--sun)"}">{m["integ"]:.1f} vs {e:.1f} kWh · {_f(mm, 1, "%")} apart</span>')}
{cap}{row("Time base", IST_OFFSET_NOTE)}</section>
<div class="notice">Energy is read from the inverter’s daily counter. “Estimated value”, CO₂ and trip loss are estimates from the tariff, factor and method shown, not meter or bill figures. N/A means plant data or earlier stored days are missing. The performance ratio uses assumed sun hours; it is not the IEC ratio.</div>"""
    return _page(site, day, gen_at, hero + kpis + plant + gen + ov + g3 + sysh + tsec + table + quality, "LIVE DATA")


def _page(site: Site, day: dt.date, gen_at: str, body: str, badge: str) -> str:
    logo = f'<img src="{LOGO}" alt="Solserva Greentech logo" style="height:74px">' if LOGO else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Solserva | Daily report {day:%d %b %Y} | {html.escape(site.client)}</title>
<style>{THEME_CSS}</style><style>{EXTRA_CSS}</style></head><body><main class="shell">
<header class="header"><div style="display:flex;align-items:center;gap:16px">{logo}<div class="brand"><div class="n1">SOLSERVA</div><div class="n2">GREENTECH PVT LTD</div><div class="sub" style="margin-top:6px">DAILY PERFORMANCE REPORT · {day:%d %b %Y}</div></div></div>
<div class="headright"><span class="pill">HELPLINE: <b><a href="tel:{"".join(c for c in site.helpline if c.isdigit() or c == "+")}" style="color:inherit;text-decoration:none">{html.escape(site.helpline)}</a></b></span><span class="pill">PLANT ID: <b>{html.escape(site.plant_id)}</b></span><span class="pill">SERVICE PLAN: <b>{html.escape(site.plan or "—")}</b></span><span class="pill"><strong>● {badge}</strong></span></div></header>
{body}
<div class="foot">Solserva Greentech Pvt Ltd · generated from live logger data · {gen_at}</div></main></body></html>"""