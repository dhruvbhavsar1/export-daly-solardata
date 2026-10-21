"""Integration tests for Phase 3 Engine processing a CollectionResult."""

import datetime
import json
from app.engine import Phase3Engine
from app.formulas import SiteConfig
from app.models import BufferedMessage, CollectionResult, CollectionStatus


def make_test_collection() -> CollectionResult:
    now_utc = datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc)
    t1_epoch = now_utc.timestamp()
    t2_epoch = t1_epoch + 300

    payload1 = {
        "headerMac": "0501060806030409",
        "timestamp": t1_epoch,
        "payload": {
            "functionId": 2,
            "functionPayload": [
                {"name": "inverterStatus", "value": 1},
                {"name": "eToday", "value": 100},
                {"name": "totalEnergy", "value": 7220},
                {"name": "activePower", "value": 820},
                {"name": "inverterTemperature", "value": 41},
            ],
        },
    }
    payload2 = {
        "headerMac": "0501060806030409",
        "timestamp": t2_epoch,
        "payload": {
            "functionId": 2,
            "functionPayload": [
                {"name": "inverterStatus", "value": 1},
                {"name": "eToday", "value": 105},
                {"name": "totalEnergy", "value": 7225},
                {"name": "activePower", "value": 850},
                {"name": "inverterTemperature", "value": 43},
            ],
        },
    }

    msg1 = BufferedMessage(
        index=1,
        topic="solar/1380242000046",
        raw_payload=json.dumps(payload1).encode("utf-8"),
        received_at=now_utc,
    )
    msg2 = BufferedMessage(
        index=2,
        topic="solar/1380242000046",
        raw_payload=json.dumps(payload2).encode("utf-8"),
        received_at=now_utc + datetime.timedelta(seconds=300),
    )

    return CollectionResult(
        status=CollectionStatus.SUCCESS,
        broker="3.7.240.91",
        port=8883,
        topic="solar/1380242000046",
        logger_serial="1380242000046",
        use_tls=False,
        connect_success=True,
        subscribe_success=True,
        messages=[msg1, msg2],
    )


def test_phase3_engine_end_to_end_pipeline():
    site_cfg = SiteConfig(
        dc_capacity_kwp=5.0,
        psh_hours_per_day=4.5,
        tariff_inr_per_kwh=8.0,
        grid_emission_factor_kg_per_kwh=0.82,
        tree_co2_kg_per_tree_year=22.55,
        expected_cadence_seconds=300,
    )
    engine = Phase3Engine(site_config=site_cfg)
    collection = make_test_collection()

    ref_time = datetime.datetime(2026, 10, 21, 12, 6, 0, tzinfo=datetime.timezone.utc)
    res = engine.process_collection(collection, reference_time=ref_time)

    assert res.total_messages == 2
    assert res.valid_samples == 2
    assert res.invalid_samples == 0
    assert res.duplicate_samples == 0
    assert res.out_of_order_samples == 0
    assert res.counter_resets == 0

    # Formulas calculated
    assert "F01" in res.calculated_formulas
    assert res.formula_results["F01"].value == 5.0  # 7225 - 7220

    assert "F02" in res.calculated_formulas
    assert res.formula_results["F02"].value == 5.0

    assert "F03" in res.calculated_formulas
    assert res.formula_results["F03"].value == 22.5  # 5 * 4.5

    assert "F17" in res.calculated_formulas
    assert res.formula_results["F17"].value == 40.0  # 5 kWh * ₹8

    assert "F21" in res.calculated_formulas
    assert res.formula_results["F21"].value == 4.1  # 5 * 0.82

    assert "F24" in res.formula_results
    assert res.formula_results["F24"].value == "ONLINE"

    assert "F25" in res.formula_results
    assert res.formula_results["F25"].value == 43

    assert "F28" in res.formula_results
    assert res.formula_results["F28"].value["inverter_status_code"] == 1

    assert "F29" in res.formula_results
    assert res.formula_results["F29"].value["coverage_pct"] == 100.0

    # Unavailable formulas returning structured N/A
    assert "F05" in res.na_formulas
    assert "Plane-of-array irradiation" in res.formula_results["F05"].missing_inputs[0]

    # Safe summary
    summary = res.safe_summary()
    assert summary["valid_samples"] == 2
    assert "password" not in str(summary)
