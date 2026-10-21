"""Unit tests for Solar Formula Engine (F01–F30), N/A handling, and unit preservation."""

import datetime
from app.formulas import FormulaResult, SiteConfig, SolarFormulaEngine
from app.parser import NormalizedTelemetry
from app.validator import ValidatedSample


def make_sample(
    index: int,
    ts_epoch: float,
    total_energy: float | int | None = None,
    e_today: float | int | None = None,
    power: float | int | None = None,
    temp: float | int | None = 42,
    status_code: int = 1,
) -> ValidatedSample:
    dt = datetime.datetime.fromtimestamp(ts_epoch, tz=datetime.timezone.utc)
    t = NormalizedTelemetry(
        message_index=index,
        topic="solar/1380242000046",
        raw_bytes=b"sample_bytes",
        received_at_utc=dt,
        telemetry_timestamp_raw=ts_epoch,
        header_mac="0501060806030409",
    )
    if total_energy is not None:
        t.scalars["total_energy_raw"] = total_energy
    if e_today is not None:
        t.scalars["energy_today_raw"] = e_today
    if power is not None:
        t.scalars["active_power_raw"] = power
    if temp is not None:
        t.scalars["inverter_temperature_raw"] = temp
    t.scalars["inverter_status_raw"] = status_code

    return ValidatedSample(
        telemetry=t,
        is_usable=True,
        timestamp_utc=dt,
    )


def test_f01_interval_energy_normal_and_resets():
    site_cfg = SiteConfig(energy_unit_verified=True, energy_unit="kWh")
    engine = SolarFormulaEngine(site_config=site_cfg)

    # Normal increasing sequence: 7220 -> 7222 -> 7225
    samples = [
        make_sample(index=1, ts_epoch=1729000000, total_energy=7220),
        make_sample(index=2, ts_epoch=1729000300, total_energy=7222),
        make_sample(index=3, ts_epoch=1729000600, total_energy=7225),
    ]
    res = engine.evaluate_all(samples)["F01"]
    assert res.status == "CALCULATED"
    assert res.value == 5.0  # (7222 - 7220) + (7225 - 7222)
    assert res.unit == "kWh"

    # Counter reset: 7225 -> 10 (e.g. inverter replacement/reset)
    samples_with_reset = samples + [
        make_sample(index=4, ts_epoch=1729000900, total_energy=10),
    ]
    res_reset = engine.evaluate_all(samples_with_reset)["F01"]
    assert res_reset.status == "CALCULATED"
    # Negative delta must NOT be added as generation
    assert res_reset.value == 5.0


def test_f01_unverified_units_returns_na():
    # Without verified energy unit, F01 returns structured N/A
    engine = SolarFormulaEngine()
    samples = [
        make_sample(index=1, ts_epoch=1729000000, total_energy=7220),
        make_sample(index=2, ts_epoch=1729000300, total_energy=7225),
    ]
    res = engine.evaluate_all(samples)["F01"]
    assert res.status == "N/A"
    assert "Verified engineering unit" in res.missing_inputs[0]


def test_f01_single_sample_returns_na():
    engine = SolarFormulaEngine(site_config=SiteConfig(energy_unit_verified=True))
    samples = [make_sample(index=1, ts_epoch=1729000000, total_energy=7220)]
    res = engine.evaluate_all(samples)["F01"]
    assert res.status == "N/A"
    assert ">= 2 consecutive valid samples" in res.missing_inputs[0]


def test_f02_period_energy_labeled_partial_so_far():
    site_cfg = SiteConfig(energy_unit_verified=True, energy_unit="kWh")
    engine = SolarFormulaEngine(site_config=site_cfg)
    samples = [
        make_sample(index=1, ts_epoch=1729000000, total_energy=100),
        make_sample(index=2, ts_epoch=1729000300, total_energy=104),
    ]
    res = engine.evaluate_all(samples)["F02"]
    assert res.status == "PARTIAL"
    assert res.value == 4.0
    assert "partial generation so far" in res.explanation.lower()
    assert "not as a complete 24-hour daily" in res.explanation.lower()


def test_f03_f04_with_and_without_site_config():
    # Without site config: returns N/A
    engine_unconfigured = SolarFormulaEngine()
    res3_na = engine_unconfigured.evaluate_all([])["F03"]
    assert res3_na.status == "N/A"
    assert "dc_capacity_kwp" in res3_na.missing_inputs

    # With site config: 5 kWp, 4.5 PSH -> 5 * 4.5 * 1.0 = 22.5 kWh/day
    site_cfg = SiteConfig(
        dc_capacity_kwp=5.0,
        psh_hours_per_day=4.5,
        energy_unit_verified=True,
        energy_unit="kWh",
    )
    engine_configured = SolarFormulaEngine(site_config=site_cfg)

    samples = [
        make_sample(index=1, ts_epoch=1729000000, total_energy=100),
        make_sample(index=2, ts_epoch=1729000300, total_energy=118),  # 18 kWh
    ]
    all_res = engine_configured.evaluate_all(samples)
    res3 = all_res["F03"]
    assert res3.status == "CALCULATED"
    assert res3.value == 22.5

    # F04: achievement: (18 / 22.5) * 100 = 80.0%
    res4 = all_res["F04"]
    assert res4.status == "CALCULATED"
    assert res4.value == 80.0


def test_f24_online_offline_status():
    site_cfg = SiteConfig(online_timeout_seconds=300)
    engine = SolarFormulaEngine(site_config=site_cfg)

    t0 = datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)
    t0_epoch = t0.timestamp()
    sample = make_sample(index=1, ts_epoch=t0_epoch)

    # Reference time 60 seconds after sample -> ONLINE
    now_online = t0 + datetime.timedelta(seconds=60)
    res_online = engine.evaluate_all([sample], reference_time=now_online)["F24"]
    assert res_online.status == "CALCULATED"
    assert res_online.value == "ONLINE"

    # Reference time 400 seconds after sample (exceeds 300s timeout) -> OFFLINE
    now_offline = t0 + datetime.timedelta(seconds=400)
    res_offline = engine.evaluate_all([sample], reference_time=now_offline)["F24"]
    assert res_offline.status == "CALCULATED"
    assert "OFFLINE" in str(res_offline.value)


def test_f25_inverter_temperature_no_false_ok():
    engine = SolarFormulaEngine()
    sample = make_sample(index=1, ts_epoch=1729000000, temp=42)
    res = engine.evaluate_all([sample])["F25"]

    assert res.status == "CALCULATED"
    assert res.value == 42
    # Verify we do not arbitrarily declare 42°C as OK without vendor spec
    assert "Operating limit not verified" in res.assumptions[0]


def test_f28_fault_status_wiring_safety_disclaimer():
    engine = SolarFormulaEngine()
    sample = make_sample(index=1, ts_epoch=1729000000, status_code=0)
    res = engine.evaluate_all([sample])["F28"]

    assert res.status == "CALCULATED"
    assert res.value["inverter_status_code"] == 0
    # Must explicitly state "not wiring verified safe"
    assert "wiring not verified safe" in res.value["alarm_status_label"].lower()


def test_f29_freshness_and_coverage():
    # 1. Unknown cadence: coverage_pct is None, does not invent percentage
    engine_unknown = SolarFormulaEngine(SiteConfig(expected_cadence_seconds=None))
    t0 = datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)
    samples = [
        make_sample(index=1, ts_epoch=t0.timestamp()),
        make_sample(index=2, ts_epoch=(t0 + datetime.timedelta(seconds=300)).timestamp()),
    ]
    res_unknown = engine_unknown.evaluate_all(samples, reference_time=t0 + datetime.timedelta(seconds=310))["F29"]
    assert res_unknown.value["coverage_pct"] is None
    assert res_unknown.value["message_count"] == 2
    assert "Cadence unknown" in res_unknown.explanation

    # 2. Configured cadence of 300s: 2 samples over 300s = 2 expected -> 100%
    engine_configured = SolarFormulaEngine(SiteConfig(expected_cadence_seconds=300))
    res_configured = engine_configured.evaluate_all(samples, reference_time=t0 + datetime.timedelta(seconds=310))["F29"]
    assert res_configured.value["coverage_pct"] == 100.0


def test_unavailable_formulas_return_structured_na():
    engine = SolarFormulaEngine()
    all_res = engine.evaluate_all([])

    # Verify that formulas requiring unsupplied inputs return N/A with clear explanations
    na_ids = ["F05", "F07", "F08", "F09", "F10", "F11", "F12", "F13", "F14", "F15", "F16", "F18", "F19", "F20", "F23", "F26", "F27", "F30"]
    for fid in na_ids:
        res = all_res[fid]
        assert res.status == "N/A"
        assert len(res.missing_inputs) > 0
        assert len(res.explanation) > 0


def test_unverified_unit_scaling_and_status_mappings():
    """Verifies that unverified scale factors and raw inverter status codes are preserved without guessing."""
    engine = SolarFormulaEngine()
    # status code 2 with 0 fault alarms
    sample = make_sample(index=1, ts_epoch=1729000000, status_code=2, temp=38)
    all_res = engine.evaluate_all([sample])

    # Inverter status preserved as raw code 2
    f28 = all_res["F28"]
    assert f28.status == "CALCULATED"
    assert f28.value["inverter_status_code"] == 2
    assert "software status only" in f28.value["alarm_status_label"].lower()
    assert "wiring not verified safe" in f28.value["alarm_status_label"].lower()

    # Temperature preserved as raw value 38 without inventing threshold OK
    f25 = all_res["F25"]
    assert f25.status == "CALCULATED"
    assert f25.value == 38
    assert "Operating limit not verified" in f25.assumptions[0]
