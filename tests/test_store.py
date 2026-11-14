import datetime as dt
import sqlite3

from app.store import Store

D = dt.date(2026, 10, 23)


def sample(h, m, p=1.0, e=1.0, mac="M1"):
    return {"mac": mac, "t": dt.datetime(2026, 10, 23, h, m), "p_kw": p, "e_kwh": e, "total_kwh": 7500, "v": 250,
            "i_a": 4.0, "hz": 50, "pf": 1.0, "mppt_v": 330, "mppt_a": 3.0, "status": 2}


def test_a_reading_is_identified_by_device_and_time():
    s = Store(":memory:")
    assert s.add_samples("CH", [sample(10, 0), sample(10, 5), sample(10, 0)]) == 2      # duplicate ignored
    assert s.add_samples("CH", [sample(10, 0, mac="M2")]) == 1                           # other device, same time: kept
    assert s.add_samples("OTHER", [sample(10, 0)]) == 0                                  # same device+time via another channel
    assert s.macs_for_day(D) == {"M1": 2, "M2": 1}
    rows = s.day_samples("M1", D)
    assert [r["t"].minute for r in rows] == [0, 5] and isinstance(rows[0]["t"], dt.datetime)
    assert rows[0]["p_kw"] == 1.0 and rows[0]["status"] == 2 and rows[0]["mac"] == "M1"


def test_other_days_are_not_mixed_in():
    s = Store(":memory:")
    s.add_samples("CH", [sample(10, 0)])
    assert s.day_samples("M1", D + dt.timedelta(days=1)) == [] and s.count_for_mac("M1", D) == 1
    assert s.last_reading_time("M1") == dt.datetime(2026, 10, 23, 10, 0) and s.last_reading_time("Z") is None
    assert s.last_reading_time() == dt.datetime(2026, 10, 23, 10, 0)


def test_devices_overview_for_matching_devices_to_customers():
    s = Store(":memory:")
    s.add_samples("CH", [sample(9, 0, p=0.5, e=1.0), sample(12, 0, p=3.0, e=10.0), sample(15, 0, p=1.0, e=18.5),
                         sample(11, 0, p=2.0, e=5.0, mac="M2")])
    ov = {d["mac"]: d for d in s.devices_overview(D)}
    assert ov["M1"]["n"] == 3 and ov["M1"]["max_e"] == 18.5 and ov["M1"]["last_p"] == 1.0 and ov["M1"]["last"].endswith("15:00:00")
    assert ov["M2"]["n"] == 1 and ov["M1"]["channel"] == "CH"


def test_summary_is_kept_and_month_and_previous_day_work():
    s = Store(":memory:")
    for day, e in ((dt.date(2026, 10, 21), 20.0), (dt.date(2026, 10, 22), 22.5), (dt.date(2026, 9, 30), 99.0)):
        s.upsert_summary("A", day, "M1", {"energy": e, "peak_kw": 3.5, "n": 130}, 132)
    assert s.prev_energy("A", D) == 22.5 and s.prev_energy("A", dt.date(2026, 10, 25)) is None
    assert s.month_energy_before("A", D) == 42.5
    s.upsert_summary("A", D, "M1", {"energy": 24.0, "peak_kw": 3.6, "n": 128}, 132)
    s.upsert_summary("A", D, "M1", {"energy": 24.5, "peak_kw": 3.6, "n": 130}, 132)
    got = s.get_summary("A", D)
    assert got["energy_kwh"] == 24.5 and got["expected"] == 132


def test_prune_removes_old_readings_but_never_summaries():
    s = Store(":memory:")
    s.add_samples("CH", [sample(10, 0)])
    s.upsert_summary("A", D, "M1", {"energy": 24.0}, 132)
    assert s.prune(90, D + dt.timedelta(days=30)) == 0
    assert s.prune(90, D + dt.timedelta(days=100)) == 1
    assert s.count_for_mac("M1", D) == 0 and s.get_summary("A", D) is not None


def test_job_marker(tmp_path):
    s = Store(tmp_path / "x" / "t.db")
    assert not s.is_finalized(D)
    s.mark_finalized(D)
    assert s.is_finalized(D)


def test_old_trial_database_is_moved_aside_not_destroyed(tmp_path):
    path = tmp_path / "solarpulse.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE readings(serial TEXT, mac TEXT, t TEXT, p_kw REAL)")
    c.execute("INSERT INTO readings VALUES('1380242000046','M1','2026-11-11 10:00:00',1.0)")
    c.commit(); c.close()
    s = Store(path)
    assert s.add_samples("CH", [sample(10, 0)]) == 1                                     # new schema works
    kept = [p for p in tmp_path.iterdir() if ".old_trial_" in p.name]
    assert len(kept) == 1
    old = sqlite3.connect(kept[0]).execute("SELECT COUNT(*) FROM readings").fetchone()[0]
    assert old == 1                                                                      # trial data still there
