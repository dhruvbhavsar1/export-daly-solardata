import datetime as dt

from app import calc, trips as tp
from app.html_report import Site, build_daily_html
from app.service import LoggerCfg, load_loggers
from app.store import Store

DAY = dt.date(2026, 11, 12)


def mk(hhmm, p, e=0.0):
    h, m = map(int, hhmm.split(":"))
    return {"mac": "A", "t": dt.datetime.combine(DAY, dt.time(h, m)), "p_kw": p, "e_kwh": e, "total_kwh": 1000 + e, "v": 230, "i_a": 5,
            "hz": 50, "pf": 100, "mppt_v": 600, "mppt_a": 5, "status": 2}


def day_with_stop(flag=True):
    s, t, e = [], dt.datetime.combine(DAY, dt.time(8, 0)), 0.0
    while t <= dt.datetime.combine(DAY, dt.time(16, 0)):
        p = 0.0 if dt.time(11, 0) <= t.time() < dt.time(12, 0) else 10.0
        e += p / 12
        s.append(mk(t.strftime("%H:%M"), p, e)); t += dt.timedelta(minutes=5)
    f = [{"t": dt.datetime.combine(DAY, dt.time(11, 20)), "active": ["NG"]}] if flag else []
    return s, f


def test_grid_trip_loss_is_straight_line():
    s, f = day_with_stop()
    (ev,) = tp.detect_trips(s, f, DAY, 7.0)
    assert ev["kind"] == tp.GRID and ev["codes"] == ["NO-Grid (1015)"]
    assert ev["minutes"] == 60 and abs(ev["loss_kwh"] - 10.0) < 0.1 and abs(ev["loss_inr"] - 70) <= 1


def test_internal_flag_and_unclassified_and_sunrise():
    s, _ = day_with_stop()
    (ev,) = tp.detect_trips(s, [{"t": dt.datetime.combine(DAY, dt.time(11, 20)), "active": ["HT"]}], DAY, 7.0)
    assert ev["kind"] == tp.INTERNAL
    (ev,) = tp.detect_trips(s, [], DAY, 7.0)                    # stop with neighbours but no alarm
    assert ev["kind"] == tp.OTHER and not ev["flagged"]
    assert tp.detect_trips([mk("07:00", 0), mk("07:05", 0), mk("07:10", 0.4), mk("07:15", 1.0)], [], DAY, 7.0) == []


def test_summary_and_calc():
    s, f = day_with_stop()
    r = tp.summarize_trips(tp.detect_trips(s, f, DAY, 7.0))
    assert r[tp.GRID]["n"] == 1 and r[tp.INTERNAL]["n"] == 0
    assert calc.cuf_class(15.9) == "red" and calc.cuf_class(16) == "orange" and calc.cuf_class(20) == "orange" and calc.cuf_class(20.1) == "green"
    assert abs(calc.cuf(412.5, 5, 31) - 11.09) < 0.01 and abs(calc.perf_ratio(412.5, 5, 4.5, 22) - 83.33) < 0.01
    hist = {dt.date(2026, 10, d): 10.0 for d in range(1, 32)} | {dt.date(2026, 11, d): 12.0 for d in range(1, 13)}
    mi = calc.month_info(hist, DAY)
    assert mi["last_month"] == 120.0 and mi["kwh"] == 144.0 and mi["last_year"] is None
    assert calc.change(144, 120) == (24, 20.0) and calc.change(1, None) is None


def test_report_contains_requested_blocks():
    s, f = day_with_stop()
    site = Site(client="X", address="Y", plant_id="P1", serial="S1", kwp=40, psh=4.5, plan="Premium", inverter_make="Solis", inverter_model="40K")
    h = build_daily_html(s, DAY, site, history={dt.date(2026, 11, 11): 50.0}, faults=f)
    for k in ("SOLSERVA", "GREENTECH PVT LTD", "Today</h2>", "ESTIMATED VALUE THIS MONTH", "CUF (Month)", "Performance Ratio", "Communication",
              "Plant &amp; Grid Trip", "Internal Inverter Trip", "Service Code (Subscription Plan)", "Day-wise", "Month-wise", "Investment ROI", "NO-Grid (1015)"):
        assert k in h, k
    assert "No data was received" in build_daily_html([], DAY, site)


def test_store_faults_trips_history_and_plants_overlay(tmp_path):
    st = Store(tmp_path / "t.db")
    st.add_fault({"mac": "A", "t": dt.datetime(2026, 11, 12, 11, 20), "active": ["NG"]})
    assert st.day_faults("A", DAY)[0]["active"] == ["NG"]
    s, f = day_with_stop()
    st.replace_trips("S1", DAY, tp.detect_trips(s, f, DAY, 7.0))
    assert len(st.trips_before("S1", DAY + dt.timedelta(days=1))) == 1 and st.trips_before("S1", DAY) == []
    (tmp_path / "loggers.json").write_text('[{"serial":"S1","name":"old","macs":[]}]')
    (tmp_path / "plants.json").write_text('[{"serial":"S1","client":"New Name","mac":"M1","kwp":40,"plan":"Premium"}]')
    (lg,) = load_loggers(tmp_path / "loggers.json")
    assert lg.name == "New Name" and lg.macs == ["M1"] and lg.kwp == 40

def test_indian_rupee_grouping_and_roi_formula():
    from app.html_report import _inr
    assert [_inr(x) for x in (0, 123, 7496, 55500, 300000, 1000000, 1234567, 12345678)] == ["0", "123", "7,496", "55,500", "3,00,000", "10,00,000", "12,34,567", "1,23,45,678"]
    s, f = day_with_stop()
    import datetime as dt
    site = Site(client="X", address="Y", plant_id="P", serial="S", kwp=40, tariff=7.0, invested_inr=1000000, commissioned=dt.date(2021, 11, 12))
    h = build_daily_html(s, DAY, site, faults=f)
    life = s[-1]["total_kwh"]
    assert f"₹{_inr(life * 7.0)}" in h and "₹10,00,000" in h                      # Recovered = lifetime kWh x tariff
    assert f"{life * 7.0 / 1000000 * 100:.1f}%" in h                              # ROI = recovered x 100 / invested
