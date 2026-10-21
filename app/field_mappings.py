"""Explicit field mapping, metadata, and verification catalog for solar MQTT telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class VerificationStatus(str, Enum):
    """Indicates whether a field's engineering units, scale, and semantics are verified."""
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class FieldMetadata:
    """Metadata detailing the schema, scale, verification status, and provenance of a telemetry field."""
    raw_name: str
    normalized_name: str
    expected_type: type
    verified_unit: Optional[str]
    scale_factor: Optional[float]
    status: VerificationStatus
    validation_rule: str
    provenance: str
    evidence_needed: Optional[str] = None


# Observed field definitions based on logger payload examination and specification PDF
FIELD_CATALOG: dict[str, FieldMetadata] = {
    "slaveId": FieldMetadata(
        raw_name="slaveId",
        normalized_name="slave_id",
        expected_type=int,
        verified_unit=None,
        scale_factor=1.0,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Integer >= 0 (Modbus address)",
        provenance="payload.functionPayload[name=slaveId]",
        evidence_needed="Inverter Modbus slave ID map",
    ),
    "deviceType": FieldMetadata(
        raw_name="deviceType",
        normalized_name="device_type",
        expected_type=int,
        verified_unit=None,
        scale_factor=1.0,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Integer >= 0",
        provenance="payload.functionPayload[name=deviceType]",
        evidence_needed="Vendor device type enumerations",
    ),
    "version": FieldMetadata(
        raw_name="version",
        normalized_name="protocol_version",
        expected_type=int,
        verified_unit=None,
        scale_factor=1.0,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Integer >= 0",
        provenance="payload.functionPayload[name=version]",
        evidence_needed="Firmware / gateway protocol specification",
    ),
    "inverterStatus": FieldMetadata(
        raw_name="inverterStatus",
        normalized_name="inverter_status_raw",
        expected_type=int,
        verified_unit="raw_code",
        scale_factor=1.0,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Integer >= 0 (numeric state code)",
        provenance="payload.functionPayload[name=inverterStatus]",
        evidence_needed="Manufacturer status code mapping (0/1/2/etc. to Running/Fault/Standby)",
    ),
    "eToday": FieldMetadata(
        raw_name="eToday",
        normalized_name="energy_today_raw",
        expected_type=(int, float),
        verified_unit="unverified_energy_units",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Non-negative numeric reading",
        provenance="payload.functionPayload[name=eToday]",
        evidence_needed="Vendor confirmation whether counter is in Wh, 0.1 kWh, or kWh",
    ),
    "totalEnergy": FieldMetadata(
        raw_name="totalEnergy",
        normalized_name="total_energy_raw",
        expected_type=(int, float),
        verified_unit="unverified_cumulative_units",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Non-negative monotonically increasing counter",
        provenance="payload.functionPayload[name=totalEnergy]",
        evidence_needed="Vendor confirmation whether cumulative lifetime energy is in kWh, 0.1 kWh, or Wh",
    ),
    "activePower": FieldMetadata(
        raw_name="activePower",
        normalized_name="active_power_raw",
        expected_type=(int, float),
        verified_unit="unverified_power_units",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Numeric reading",
        provenance="payload.functionPayload[name=activePower]",
        evidence_needed="Vendor confirmation of Watts vs kW vs deci-Watts",
    ),
    "reactivePower": FieldMetadata(
        raw_name="reactivePower",
        normalized_name="reactive_power_raw",
        expected_type=(int, float),
        verified_unit="unverified_reactive_power_units",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Numeric reading",
        provenance="payload.functionPayload[name=reactivePower]",
        evidence_needed="Vendor confirmation of VAr vs kVAr",
    ),
    "apparentPower": FieldMetadata(
        raw_name="apparentPower",
        normalized_name="apparent_power_raw",
        expected_type=(int, float),
        verified_unit="unverified_apparent_power_units",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Numeric reading >= 0",
        provenance="payload.functionPayload[name=apparentPower]",
        evidence_needed="Vendor confirmation of VA vs kVA",
    ),
    "powerFactor": FieldMetadata(
        raw_name="powerFactor",
        normalized_name="power_factor_raw",
        expected_type=(int, float),
        verified_unit="unverified_pf_scale",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Numeric reading",
        provenance="payload.functionPayload[name=powerFactor]",
        evidence_needed="Vendor confirmation whether scale is 0-100 or 0.00-1.00",
    ),
    "gridFrequency": FieldMetadata(
        raw_name="gridFrequency",
        normalized_name="grid_frequency_raw",
        expected_type=(int, float),
        verified_unit="unverified_frequency_scale",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Numeric reading > 0",
        provenance="payload.functionPayload[name=gridFrequency]",
        evidence_needed="Vendor confirmation of scale factor (e.g., 49 = 49 Hz vs 5002 = 50.02 Hz)",
    ),
    "inverterTemperature": FieldMetadata(
        raw_name="inverterTemperature",
        normalized_name="inverter_temperature_raw",
        expected_type=(int, float),
        verified_unit="unverified_temp_scale",
        scale_factor=None,
        status=VerificationStatus.UNVERIFIED,
        validation_rule="Finite numeric value",
        provenance="payload.functionPayload[name=inverterTemperature]",
        evidence_needed="Vendor confirmation whether unit is °C or 0.1°C and manufacturer operating limits",
    ),
    "phaseCount": FieldMetadata(
        raw_name="phaseCount",
        normalized_name="phase_count",
        expected_type=int,
        verified_unit="phases",
        scale_factor=1.0,
        status=VerificationStatus.VERIFIED,
        validation_rule="Integer between 1 and 3",
        provenance="payload.functionPayload[name=phaseCount]",
    ),
    "mpptCount": FieldMetadata(
        raw_name="mpptCount",
        normalized_name="mppt_count",
        expected_type=int,
        verified_unit="trackers",
        scale_factor=1.0,
        status=VerificationStatus.VERIFIED,
        validation_rule="Integer >= 1",
        provenance="payload.functionPayload[name=mpptCount]",
    ),
}


def get_field_metadata(raw_name: str) -> Optional[FieldMetadata]:
    """Retrieves metadata definition for a known raw field."""
    return FIELD_CATALOG.get(raw_name)
