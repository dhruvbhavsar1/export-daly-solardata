"""Daily HTML report in the SolarPulse theme (self-contained, interactive chart).

Input : list of samples (see `parse_message`) for ONE IST calendar day.
Output: one HTML file. No external requests. Opens fine from email attachment/GitHub.

Rules kept from the project: zero is not missing; missing is never shown as zero;
unverified numbers carry a tag; money is called "Estimated value", never "savings".
"""
from __future__ import annotations

import datetime as dt
import html
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


def build_daily_html(samples: list[dict], day: dt.date, site: Site,
                     prev_energy: Optional[float] = None,
                     month_energy: Optional[float] = None,
                     generated_at: Optional[dt.datetime] = None) -> str:
    s = dedupe([x for x in samples if x["t"].date() == day])
    m = summarize(s, site)
    esc = html.escape
    gen_at = (generated_at or dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST")
    if not s:
        body = '<div class="notice">No data was received for this day. Nothing is shown as zero.</div>'
        return _page(site, day, gen_at, body, "NO DATA")

    n_exp = int((dt.datetime.combine(day, site.window_end) - dt.datetime.combine(day, site.window_start)).total_seconds() / 300)
    in_win = [x for x in s if site.window_start <= x["t"].time() <= site.window_end]
    cover = len(in_win) / n_exp * 100
    mm = m["mismatch_pct"]
    scale_ok = mm is not None and mm <= 10
    month_txt = _f(month_energy, 1, " kWh") if month_energy is not None else "N/A"

    hero = f"""
<section class="savings-feature"><div class="savings-story">
<h2>Your Solar<br/>Generated <em>{m['energy']:.1f} kWh</em><br/>Today</h2>
<p>{esc(site.client)}'s plant produced {m['energy']:.1f} kWh on {day:%A, %d %B %Y}, peaking at {m['peak_kw']:.2f} kW around {m['peak_t']:%H:%M}.
Generation ran from {m['first']:%H:%M} to {m['last']:%H:%M}. Estimated value at ₹{site.tariff:g}/kWh: ₹{m['value_inr']:,.0f}.</p></div>
<div class="savings-metrics">
<div class="savings-tile yellow"><strong>{m['energy']:.1f}</strong><span>KWH TODAY</span></div>
<div class="savings-tile black"><strong>{m['peak_kw']:.2f}</strong><span>PEAK KW</span></div>
<div class="savings-tile stone"><strong>₹{m['value_inr']:,.0f}</strong><span>ESTIMATED VALUE</span></div>
<div class="savings-tile white"><strong>{m['co2']:.0f}kg</strong><span>CO₂ AVOIDED (EST.)</span></div></div></section>"""

    kpis = f"""
<section class="grid kpis">
<div class="card" style="--accent:var(--sun)"><small>Today’s Generation</small><div class="value">{m['energy']:.1f} <span style="font-size:16px">kWh</span></div><div class="note">vs yesterday: {_delta(m['energy'], prev_energy)}</div></div>
<div class="card" style="--accent:var(--cyan)"><small>Peak Power</small><div class="value">{m['peak_kw']:.2f} <span style="font-size:16px">kW</span></div><div class="note">at {m['peak_t']:%H:%M} IST</div></div>
<div class="card" style="--accent:var(--green)"><small>Estimated Value</small><div class="value">₹{m['value_inr']:,.0f}</div><div class="note">At ₹{site.tariff:g}/kWh (estimate)</div></div>
<div class="card" style="--accent:var(--pink)"><small>Specific Yield</small><div class="value">{_f(m['yield_kwh_kwp'],2)} <span style="font-size:16px">{'' if m['yield_kwh_kwp'] is None else 'kWh/kWp'}</span></div><div class="note">{'Plant kWp not set' if site.kwp is None else f'On {site.kwp:g} kWp'}</div></div>
<div class="card" style="--accent:var(--green)"><small>CO₂ Avoided (estimate)</small><div class="value">{m['co2']:.1f} <span style="font-size:16px">kg</span></div><div class="note">Assumed {site.co2_kg_per_kwh} kg/kWh</div></div>
</section>"""

    plant = f"""
<h2 class="section-title">01 / Plant information</h2><section class="panel"><div class="fieldgrid">
<div class="field"><span>Name of Client</span><b>{esc(site.client)}</b></div>
<div class="field"><span>Address of Plant</span><b>{esc(site.address)}</b></div>
<div class="field"><span>Plant ID</span><b>{esc(site.plant_id)}</b></div>
<div class="field"><span>Capacity of Plant</span><b>{'N/A <small class="dim">(not configured)</small>' if site.kwp is None else f'{site.kwp:g} kWp'}</b></div>
<div class="field"><span>Inverter</span><b>{esc(site.inverter)}</b></div>
<div class="field"><span>Data Logger</span><b>{esc(site.serial)}</b></div></div></section>"""

    gen = f"""
<h2 class="section-title">02 / Generation data</h2>
<section class="cols"><div class="terminal"><div class="terminaltitle">&gt; GENERATION_DATA</div>
<div class="big">{m['energy']:.1f}</div><div class="unit">kWh today</div>
<div class="termrow"><span>FIRST LIGHT...</span><span>{m['first']:%H:%M}</span></div>
<div class="termrow"><span>LAST LIGHT....</span><span>{m['last']:%H:%M}</span></div>
<div class="termrow"><span>PEAK.........</span><span>{m['peak_kw']:.2f} kW</span></div>
<div class="termrow"><span>LIFETIME.....</span><span>{_f(m['total_kwh'],0,' kWh')}</span></div></div>
<div class="panel"><h2>Daily comparison</h2><div class="caption">From the inverter’s own daily energy counter</div>
<div class="row"><span>Today’s Generation</span><b>{m['energy']:.1f} kWh</b></div>
<div class="row"><span>+ / − From Yesterday</span>{_delta(m['energy'], prev_energy)}</div>
<div class="row"><span>Yesterday</span><b>{_f(prev_energy,1,' kWh')}</b></div>
<div class="row"><span>This Month so far</span><b>{month_txt}</b></div>
<div class="row"><span>Lifetime energy</span><b>{_f(m['total_kwh'],0,' kWh')}</b></div></div></section>
<section class="panel" style="margin-top:18px">{_chart(s, day, site)}</section>"""

    health = f"""
<h2 class="section-title">03 / System health (while generating)</h2>
<section class="panel"><div class="badges">
<div class="status"><span>Grid voltage (range)</span><b>{_f(m['v_min'],0)}–{_f(m['v_max'],0)} V</b></div>
<div class="status"><span>Grid frequency</span><b>{' / '.join(str(h) for h in m['hz_set']) or 'N/A'} Hz</b></div>
<div class="status"><span>Max AC current</span><b>{_f(m['i_max'],1,' A')}</b></div>
<div class="status"><span>Max DC string voltage (MPPT 1)</span><b>{_f(m['mppt_v_max'],0,' V')}</b></div>
<div class="status"><span>Inverter temperature</span><b class="warn">Not reported by inverter</b></div>
<div class="status"><span>Fault flags</span><b>None received</b></div></div></section>"""

    quality = f"""
<h2 class="section-title">04 / Data quality</h2><section class="panel">
<div class="row"><span>Readings received in {site.window_start:%H:%M}–{site.window_end:%H:%M}</span><b>{len(in_win)} of ~{n_exp} ({cover:.0f}%)</b></div>
<div class="row"><span>Gaps longer than 10 min</span><b>{m['gaps']}</b></div>
<div class="row"><span>Energy cross-check (power × time vs counter)</span><b style="color:{'var(--green)' if scale_ok else 'var(--sun)'}">{m['integ']:.1f} vs {m['energy']:.1f} kWh · {_f(mm,1,'%')} apart</b></div>
<div class="row"><span>Time base</span><b>{IST_OFFSET_NOTE}</b></div></section>
<div class="notice">Energy is read from the inverter’s daily counter, so it covers the whole day even if a few readings are missing. “Estimated value” and CO₂ use the tariff and factor shown on this page; they are estimates, not meter or bill figures.</div>"""

    rows = "".join(
        f'<tr><td>{x["t"]:%H:%M}</td><td>{x["p_kw"]:.3f}</td><td>{x["e_kwh"]:.2f}</td><td>{_f(x["v"],0)}</td><td>{_f(x["i_a"],2)}</td><td>{_f(x["mppt_v"],0)}</td><td>{_f(x["mppt_a"],2)}</td></tr>'
        for x in in_win)
    table = f"""
<h2 class="section-title">05 / Readings</h2><section class="panel"><details><summary style="cursor:pointer;font-weight:700">Show {len(in_win)} readings ({site.window_start:%H:%M}–{site.window_end:%H:%M})</summary>
<div style="overflow-x:auto;margin-top:14px"><table style="width:100%;border-collapse:collapse;font-size:13px">
<thead><tr style="text-align:left;color:var(--muted)"><th>Time</th><th>Power kW</th><th>Today kWh</th><th>AC V</th><th>AC A</th><th>DC V</th><th>DC A</th></tr></thead>
<tbody>{rows}</tbody></table></div></details></section>
<style>td,th{{padding:7px 10px;border-bottom:1px solid var(--border)}}</style>"""

    return _page(site, day, gen_at, hero + kpis + plant + gen + health + quality + table, "LIVE DATA")


def _page(site: Site, day: dt.date, gen_at: str, body: str, badge: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Solar Pulse | Daily report {day:%d %b %Y} | {html.escape(site.client)}</title>
<style>{THEME_CSS}</style><style>@media print{{.panel,.card,.terminal,.savings-feature,.notice{{break-inside:avoid}}.section-title{{break-after:avoid}}}}</style></head><body><main class="shell">
<header class="header"><div><div class="logo"><b>☀</b> SOLAR<span style="color:#ffbd52">PULSE</span></div><div class="sub">DAILY PERFORMANCE REPORT</div></div>
<div class="headright"><span class="pill">PLANT ID: <b>{html.escape(site.plant_id)}</b></span><span class="pill">LOGGER: <b>{html.escape(site.serial)}</b></span><span class="pill"><strong>● {badge}</strong></span></div></header>
<div class="intro"><div><h1>Your solar day, clearly explained.</h1><p>{day:%A, %d %B %Y} · report window {site.window_start:%H:%M}–{site.window_end:%H:%M} IST</p></div><div class="tag">Generated {gen_at}</div></div>
{body}
<div class="foot">Solar Pulse · generated from live logger data · {gen_at}</div></main></body></html>"""
