"""Unit tests for telemetry parsing, repeated phase/MPPT retention, and encoding handling."""

import datetime
import json
from app.models import BufferedMessage
from app.parser import TelemetryParser


def make_buffered_message(payload_bytes: bytes, index: int = 1) -> BufferedMessage:
    return BufferedMessage(
        index=index,
        topic="solar/test_logger",
        raw_payload=payload_bytes,
        received_at=datetime.datetime(2026, 10, 21, 10, 0, 0, tzinfo=datetime.timezone.utc),
    )


def test_parse_valid_observed_style_payload():
    raw_dict = {
        "headerMac": "0501060806030409",
        "timestamp": 1791571200,
        "payload": {
            "functionId": 2,
            "endpointId": 1,
            "applicationId": 30,
            "functionPayload": [
                {"name": "slaveId", "value": 1},
                {"name": "inverterStatus", "value": 2},
                {"name": "eToday", "value": 27200},
                {"name": "totalEnergy", "value": 7220},
                {"name": "activePower", "value": 823},
                {"name": "gridFrequency", "value": 49},
                {"name": "inverterTemperature", "value": 42},
                {"name": "phaseCount", "value": 3},
                {"name": "phaseNumber", "value": 1},
                {"name": "phaseVoltage1", "value": 249},
                {"name": "phaseCurrent1", "value": 400},
                {"name": "phaseNumber", "value": 2},
                {"name": "phaseVoltage2", "value": 248},
                {"name": "phaseCurrent2", "value": 405},
                {"name": "phaseNumber", "value": 3},
                {"name": "phaseVoltage3", "value": 250},
                {"name": "phaseCurrent3", "value": 395},
                {"name": "mpptCount", "value": 2},
                {"name": "mpptNumber", "value": 1},
                {"name": "mpptVoltage1", "value": 380},
                {"name": "mpptCurrent1", "value": 12},
                {"name": "mpptPower1", "value": 4560},
                {"name": "mpptNumber", "value": 2},
                {"name": "mpptVoltage2", "value": 375},
                {"name": "mpptCurrent2", "value": 11},
                {"name": "mpptPower2", "value": 4125},
            ],
        },
    }
    raw_bytes = json.dumps(raw_dict).encode("utf-8")
    msg = make_buffered_message(raw_bytes)

    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert telemetry.is_valid is True
    assert telemetry.header_mac == "0501060806030409"
    assert telemetry.telemetry_timestamp_raw == 1791571200
    assert telemetry.function_id == 2
    assert telemetry.get_scalar("inverter_status_raw") == 2
    assert telemetry.get_scalar("total_energy_raw") == 7220
    assert telemetry.get_scalar("active_power_raw") == 823
    assert telemetry.get_scalar("inverter_temperature_raw") == 42

    # Verify all 3 phases are preserved without overwriting
    assert len(telemetry.phases) == 3
    assert telemetry.phases[0].phase_number == 1
    assert telemetry.phases[0].voltage_raw == 249
    assert telemetry.phases[1].phase_number == 2
    assert telemetry.phases[1].voltage_raw == 248
    assert telemetry.phases[2].phase_number == 3
    assert telemetry.phases[2].voltage_raw == 250

    # Verify both MPPTs are preserved without overwriting
    assert len(telemetry.mppts) == 2
    assert telemetry.mppts[0].mppt_number == 1
    assert telemetry.mppts[0].voltage_raw == 380
    assert telemetry.mppts[0].power_raw == 4560
    assert telemetry.mppts[1].mppt_number == 2
    assert telemetry.mppts[1].voltage_raw == 375
    assert telemetry.mppts[1].power_raw == 4125

    # Verify lossless raw bytes retention
    assert telemetry.raw_bytes == raw_bytes


def test_parse_missing_and_null_fields():
    # Payload with null and missing fields
    raw_dict = {
        "headerMac": "MAC_ABC",
        "timestamp": None,
        "payload": {
            "functionId": 1,
            "functionPayload": [
                {"name": "eToday", "value": None},
                {"name": "totalEnergy", "value": 100},
            ],
        },
    }
    msg = make_buffered_message(json.dumps(raw_dict).encode("utf-8"))
    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert telemetry.is_valid is True
    assert telemetry.telemetry_timestamp_raw is None
    assert telemetry.get_scalar("energy_today_raw") is None
    assert telemetry.get_scalar("total_energy_raw") == 100
    assert len(telemetry.phases) == 0
    assert len(telemetry.mppts) == 0


def test_parse_malformed_json():
    bad_json_bytes = b'{"headerMac": "ABC", "payload": { INVALID JSON ...'
    msg = make_buffered_message(bad_json_bytes)
    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert telemetry.is_valid is False
    assert any("Malformed JSON" in w for w in telemetry.parsing_warnings)
    assert telemetry.raw_bytes == bad_json_bytes


def test_parse_unexpected_encoding_binary():
    binary_bytes = b"\x80\xff\xfe\xab\xcd\xef\x00"
    msg = make_buffered_message(binary_bytes)
    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert telemetry.is_valid is False
    assert any("not valid UTF-8" in w for w in telemetry.parsing_warnings)
    assert telemetry.raw_bytes == binary_bytes


def test_parse_repeated_entries_and_unknown_fields():
    raw_dict = {
        "headerMac": "MAC_REPEAT",
        "timestamp": 1729000000,
        "payload": {
            "functionPayload": [
                {"name": "slaveId", "value": 1},
                {"name": "slaveId", "value": 1},  # Repeated slaveId
                {"name": "completelyUnknownSensor", "value": 999},  # Unrecognized field
            ]
        },
    }
    msg = make_buffered_message(json.dumps(raw_dict).encode("utf-8"))
    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert telemetry.is_valid is True
    # Both entries preserved in raw_entries
    assert len(telemetry.raw_entries) == 3
    # Warning generated for duplicate
    assert any("appeared multiple times" in w for w in telemetry.parsing_warnings)
    # Unknown field tracked in unknown_fields
    assert len(telemetry.unknown_fields) == 1
    assert telemetry.unknown_fields[0]["name"] == "completelyUnknownSensor"


def test_parse_non_finite_numeric_values():
    # Test handling of NaN / Inf
    raw_dict = {
        "headerMac": "MAC_INF",
        "timestamp": 1729000000,
        "payload": {
            "functionPayload": [
                {"name": "totalEnergy", "value": float("nan")},
                {"name": "activePower", "value": float("inf")},
            ]
        },
    }
    # Use allow_nan=True to encode, then verify parser cleans it
    raw_bytes = json.dumps(raw_dict).encode("utf-8")
    msg = make_buffered_message(raw_bytes)
    telemetry = TelemetryParser.parse_buffered_message(msg)

    assert any("non-finite value" in w for w in telemetry.parsing_warnings)
    assert telemetry.get_scalar("total_energy_raw") is None
    assert telemetry.get_scalar("active_power_raw") is None
