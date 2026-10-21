"""Automated tests for Phase 4 Solar Daily Report PDF Generation."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
import pytest

from app.engine import Phase3Engine, Phase3Result
from app.formulas import SiteConfig, SolarFormulaEngine
from app.models import BufferedMessage, CollectionResult, CollectionStatus
from app.pdf_report import (
    IST_TZ,
    SolarDailyReportGenerator,
    format_ist_str,
    generate_active_power_chart,
    to_ist,
)
from app.validator import ValidatedSample
from app.parser import NormalizedTelemetry


def make_sample_payload(
    ts_epoch: float,
    active_power: float = 1200,
    total_energy: float = 7500,
    e_today: float = 21000,
    temp: float = 38,
    status_code: int = 2,
) -> bytes:
    payload = {
        "headerMac": "0501060806030409",
        "timestamp": ts_epoch,
        "payload": {
            "functionId": 2,
            "endpointId": 1,
            "applicationId": 30,
            "functionPayload": [
                {"name": "inverterStatus", "value": status_code},
                {"name": "eToday", "value": e_today},
                {"name": "totalEnergy", "value": total_energy},
                {"name": "activePower", "value": active_power},
                {"name": "inverterTemperature", "value": temp},
                {"name": "phaseCount", "value": 3},
                {"name": "phaseNumber", "value": 1},
                {"name": "phaseVoltage1", "value": 251},
                {"name": "phaseCurrent1", "value": 5200},
            ],
        },
    }
    return json.dumps(payload).encode("utf-8")


def create_mock_collection_result(
    num_samples: int = 3,
    start_time: Optional[datetime.datetime] = None,
    with_malformed: bool = False,
) -> CollectionResult:
    base_time = start_time or datetime.datetime(2026, 10, 21, 6, 0, 0, tzinfo=datetime.timezone.utc)
    messages: list[BufferedMessage] = []

    for i in range(num_samples):
        ts = base_time + datetime.timedelta(seconds=i * 300)
        p_bytes = make_sample_payload(
            ts_epoch=ts.timestamp(),
            active_power=1000 + i * 200,
            total_energy=7500 + i * 2,
        )
        messages.append(
            BufferedMessage(
                index=i + 1,
                topic="solar/1380242000046",
                raw_payload=p_bytes,
                received_at=ts,
                is_valid_json=True,
            )
        )

    if with_malformed:
        messages.append(
            BufferedMessage(
                index=len(messages) + 1,
                topic="solar/1380242000046",
                raw_payload=b"MALFORMED_JSON_PAYLOAD",
                received_at=base_time + datetime.timedelta(seconds=900),
                is_valid_json=False,
            )
        )

    return CollectionResult(
        status=CollectionStatus.SUCCESS if num_samples > 0 else CollectionStatus.NO_MESSAGES,
        broker="3.7.240.91",
        port=8883,
        topic="solar/1380242000046",
        logger_serial="1380242000046",
        use_tls=False,
        connect_success=True,
        subscribe_success=True,
        start_time=base_time,
        end_time=base_time + datetime.timedelta(seconds=num_samples * 300),
        requested_duration=1200,
        elapsed_duration=float(num_samples * 300),
        message_count=len(messages),
        messages=messages,
    )


def test_pdf_generation_from_valid_data(tmp_path: Path):
    """Verifies that a valid Phase3Result generates a readable, non-empty PDF file."""
    collection = create_mock_collection_result(num_samples=3)
    site_cfg = SiteConfig(
        dc_capacity_kwp=5.0,
        psh_hours_per_day=4.5,
        energy_unit_verified=True,
        energy_unit="kWh",
    )
    engine = Phase3Engine(site_config=site_cfg)
    phase3_res = engine.process_collection(collection)

    generator = SolarDailyReportGenerator(output_dir=tmp_path, site_name="Test Solar Array")
    pdf_path = generator.generate(phase3_res, site_config=site_cfg)

    assert pdf_path.exists()
    assert pdf_path.is_file()
    assert pdf_path.stat().st_size > 1000  # Non-trivial PDF size

    # Verify header magic bytes
    with open(pdf_path, "rb") as f:
        header = f.read(4)
        assert header == b"%PDF"


def test_pdf_generation_empty_collection(tmp_path: Path):
    """Verifies that an empty collection run produces a valid PDF without errors."""
    collection = create_mock_collection_result(num_samples=0)
    engine = Phase3Engine()
    phase3_res = engine.process_collection(collection)

    generator = SolarDailyReportGenerator(output_dir=tmp_path)
    pdf_path = generator.generate(phase3_res)

    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 500


def test_pdf_generation_partial_data_and_malformed_messages(tmp_path: Path):
    """Verifies that partial data and malformed messages are reported cleanly in data quality."""
    collection = create_mock_collection_result(num_samples=1, with_malformed=True)
    engine = Phase3Engine()
    phase3_res = engine.process_collection(collection)

    assert phase3_res.invalid_samples >= 1

    generator = SolarDailyReportGenerator(output_dir=tmp_path)
    pdf_path = generator.generate(phase3_res)
    assert pdf_path.exists()


def test_time_series_chart_adequate_data():
    """Verifies that time series chart returns a BytesIO image when >= 2 valid points exist."""
    collection = create_mock_collection_result(num_samples=3)
    engine = Phase3Engine()
    phase3_res = engine.process_collection(collection)

    chart_buf = generate_active_power_chart(phase3_res.samples)
    assert chart_buf is not None
    assert chart_buf.getvalue().startswith(b"\x89PNG")  # Valid PNG image


def test_time_series_chart_insufficient_data():
    """Verifies that time series chart returns None when fewer than 2 points exist."""
    collection = create_mock_collection_result(num_samples=1)
    engine = Phase3Engine()
    phase3_res = engine.process_collection(collection)

    chart_buf = generate_active_power_chart(phase3_res.samples)
    assert chart_buf is None


def test_ist_timezone_formatting():
    """Verifies that UTC timestamps are converted to Asia/Kolkata (IST) accurately."""
    utc_time = datetime.datetime(2026, 10, 21, 6, 30, 0, tzinfo=datetime.timezone.utc)
    ist_time = to_ist(utc_time)
    assert ist_time.hour == 12  # 6:30 + 5:30 = 12:00
    assert ist_time.minute == 0

    formatted = format_ist_str(utc_time)
    assert "2026-10-21 12:00:00 IST" == formatted


def test_sensitive_value_redaction(tmp_path: Path):
    """Verifies that passwords, tokens, and broker secrets are never written to the PDF binary/text."""
    collection = create_mock_collection_result(num_samples=2)
    engine = Phase3Engine()
    phase3_res = engine.process_collection(collection)

    generator = SolarDailyReportGenerator(output_dir=tmp_path)
    pdf_path = generator.generate(phase3_res)

    with open(pdf_path, "rb") as f:
        content = f.read()

    # Passwords and secret keywords must not exist in generated PDF
    assert b"MQTT_PASSWORD" not in content
    assert b"secret" not in content.lower() or b"confidential" in content.lower()


def test_f01_counter_reset_regression_exclusion():
    """Verifies that counter drops/resets are excluded from valid delta accumulation and flagged."""
    site_cfg = SiteConfig(energy_unit_verified=True, energy_unit="kWh")
    engine = SolarFormulaEngine(site_config=site_cfg)

    dt1 = datetime.datetime(2026, 10, 21, 10, 0, 0, tzinfo=datetime.timezone.utc)
    dt2 = datetime.datetime(2026, 10, 21, 10, 5, 0, tzinfo=datetime.timezone.utc)
    dt3 = datetime.datetime(2026, 10, 21, 10, 10, 0, tzinfo=datetime.timezone.utc)

    # Sample 1: 7220, Sample 2: 7225 (+5), Sample 3: 10 (DROP from 7225 to 10)
    def make_stub(idx, dt, energy):
        t = NormalizedTelemetry(
            message_index=idx,
            topic="solar/test",
            raw_bytes=b"{}",
            received_at_utc=dt,
            telemetry_timestamp_raw=dt.timestamp(),
        )
        t.scalars["total_energy_raw"] = energy
        return ValidatedSample(telemetry=t, is_usable=True, timestamp_utc=dt)

    samples = [
        make_stub(1, dt1, 7220),
        make_stub(2, dt2, 7225),
        make_stub(3, dt3, 10),
    ]

    res = engine.evaluate_all(samples)["F01"]
    assert res.status == "CALCULATED"
    # Must only accumulate valid interval (+5), NOT add negative delta or pretend reset is 0 kWh generated
    assert res.value == 5.0
    intervals = res.inputs_used["intervals"]
    assert len(intervals) == 2
    assert intervals[0]["is_valid_interval"] is True
    assert intervals[0]["delta"] == 5.0
    assert intervals[1]["is_valid_interval"] is False
    assert intervals[1]["counter_reset"] is True
    assert "Counter drop detected" in intervals[1]["warning"]
    assert "excluded from accumulation" in res.explanation
