"""Unit tests for sample validator, duplicate detection, and counter monotonicity."""

import datetime
from app.parser import NormalizedTelemetry
from app.validator import TelemetryValidator


def create_telemetry_stub(
    index: int,
    ts: float | int | None,
    total_energy: float | int | None = 1000,
    mac: str = "MAC_TEST",
    raw_bytes: bytes = b"test_payload",
) -> NormalizedTelemetry:
    t = NormalizedTelemetry(
        message_index=index,
        topic="solar/test",
        raw_bytes=raw_bytes,
        received_at_utc=datetime.datetime(2026, 10, 21, 12, 0, 0, tzinfo=datetime.timezone.utc),
        telemetry_timestamp_raw=ts,
        header_mac=mac,
    )
    if total_energy is not None:
        t.scalars["total_energy_raw"] = total_energy
    return t


def test_validator_missing_and_malformed_timestamp():
    validator = TelemetryValidator()

    # 1. Missing timestamp
    t1 = create_telemetry_stub(index=1, ts=None)
    res1 = validator.validate_sample(t1)
    assert res1.is_usable is False
    assert any("Missing telemetry timestamp" in w for w in res1.validation_warnings)

    # 2. Malformed non-numeric timestamp
    t2 = create_telemetry_stub(index=2, ts="not_a_timestamp")
    res2 = validator.validate_sample(t2)
    assert res2.is_usable is False
    assert any("Malformed non-numeric timestamp" in w for w in res2.validation_warnings)


def test_validator_duplicate_message_detection():
    validator = TelemetryValidator()

    t1 = create_telemetry_stub(index=1, ts=1729000000, raw_bytes=b"identical_payload")
    t2 = create_telemetry_stub(index=2, ts=1729000000, raw_bytes=b"identical_payload")  # Exact duplicate

    res1 = validator.validate_sample(t1)
    res2 = validator.validate_sample(t2)

    assert res1.is_duplicate is False
    assert res1.is_usable is True

    assert res2.is_duplicate is True
    assert res2.is_usable is False
    assert any("Duplicate message detected" in w for w in res2.validation_warnings)


def test_validator_out_of_order_samples():
    validator = TelemetryValidator()

    # Sample 1: t=100
    t1 = create_telemetry_stub(index=1, ts=1729000100, raw_bytes=b"p1")
    # Sample 2: t=90 (arrived later but with earlier timestamp)
    t2 = create_telemetry_stub(index=2, ts=1729000090, raw_bytes=b"p2")

    res1 = validator.validate_sample(t1)
    res2 = validator.validate_sample(t2)

    assert res1.is_out_of_order is False
    assert res2.is_out_of_order is True
    assert any("Out-of-order sample" in w for w in res2.validation_warnings)


def test_validator_counter_increases_unchanged_and_resets():
    validator = TelemetryValidator()

    # Sample 1: total_energy = 1000
    s1 = validator.validate_sample(create_telemetry_stub(index=1, ts=100, total_energy=1000, raw_bytes=b"1"))
    assert s1.has_counter_reset is False

    # Sample 2: total_energy = 1005 (normal increase)
    s2 = validator.validate_sample(create_telemetry_stub(index=2, ts=200, total_energy=1005, raw_bytes=b"2"))
    assert s2.has_counter_reset is False

    # Sample 3: total_energy = 1005 (unchanged counter)
    s3 = validator.validate_sample(create_telemetry_stub(index=3, ts=300, total_energy=1005, raw_bytes=b"3"))
    assert s3.has_counter_reset is False

    # Sample 4: total_energy = 20 (counter reset or rollover)
    s4 = validator.validate_sample(create_telemetry_stub(index=4, ts=400, total_energy=20, raw_bytes=b"4"))
    assert s4.has_counter_reset is True
    assert any("Counter reset or rollover detected" in w for w in s4.validation_warnings)


def test_validator_negative_values_handling():
    validator = TelemetryValidator()

    # Negative totalEnergy is invalid
    t_neg = create_telemetry_stub(index=1, ts=100, total_energy=-50, raw_bytes=b"neg")
    res = validator.validate_sample(t_neg)
    assert res.is_usable is False
    assert any("Negative cumulative energy" in w for w in res.validation_warnings)


def test_validator_does_not_silently_delete_samples():
    validator = TelemetryValidator()
    samples = [
        create_telemetry_stub(index=1, ts=100, total_energy=1000, raw_bytes=b"a"),
        create_telemetry_stub(index=2, ts=None, total_energy=1005, raw_bytes=b"b"),  # malformed ts
        create_telemetry_stub(index=3, ts=200, total_energy=-10, raw_bytes=b"c"),   # negative counter
    ]
    report = validator.validate_series(samples)

    assert report.total_samples == 3
    assert len(report.samples) == 3
    assert report.valid_samples == 1
    assert report.invalid_samples == 2
