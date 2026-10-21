"""Robust MQTT payload parser for solar logger telemetry packets."""

from __future__ import annotations

import datetime
import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.field_mappings import FIELD_CATALOG, get_field_metadata
from app.models import BufferedMessage


@dataclass
class PhaseRecord:
    """Telemetry record for a single AC grid phase."""
    phase_number: int
    voltage_raw: Optional[float | int] = None
    current_raw: Optional[float | int] = None
    provenance: str = ""


@dataclass
class MPPTRecord:
    """Telemetry record for a single Maximum Power Point Tracker (MPPT)."""
    mppt_number: int
    voltage_raw: Optional[float | int] = None
    current_raw: Optional[float | int] = None
    power_raw: Optional[float | int] = None
    provenance: str = ""


@dataclass
class NormalizedTelemetry:
    """
    Structured, normalized representation of a single telemetry packet.

    Retains original raw bytes, distinguishes local receipt time from logger timestamp,
    and preserves multi-phase and multi-MPPT records without data loss.
    """
    message_index: int
    topic: str
    raw_bytes: bytes
    received_at_utc: datetime.datetime
    telemetry_timestamp_raw: Optional[int | float | str] = None
    header_mac: Optional[str] = None
    function_id: Optional[int] = None
    endpoint_id: Optional[int] = None
    application_id: Optional[int] = None
    scalars: dict[str, Any] = field(default_factory=dict)
    phases: list[PhaseRecord] = field(default_factory=list)
    mppts: list[MPPTRecord] = field(default_factory=list)
    raw_entries: list[dict[str, Any]] = field(default_factory=list)
    unknown_fields: list[dict[str, Any]] = field(default_factory=list)
    parsing_warnings: list[str] = field(default_factory=list)
    is_valid: bool = True

    def get_scalar(self, name: str, default: Any = None) -> Any:
        """Retrieves a normalized scalar value safely."""
        return self.scalars.get(name, default)


class TelemetryParser:
    """Safely decodes and validates MQTT JSON telemetry payloads."""

    @classmethod
    def parse_buffered_message(cls, message: BufferedMessage) -> NormalizedTelemetry:
        """
        Parses a single BufferedMessage into NormalizedTelemetry.

        Ensures raw bytes are preserved lossless, handles malformed JSON,
        and avoids crashing on unexpected payload structures.
        """
        raw_bytes = message.raw_payload
        result = NormalizedTelemetry(
            message_index=message.index,
            topic=message.topic,
            raw_bytes=raw_bytes,
            received_at_utc=message.received_at,
        )

        # 1. Attempt UTF-8 decoding
        try:
            decoded_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as err:
            result.is_valid = False
            result.parsing_warnings.append(f"Payload is not valid UTF-8: {err}")
            return result

        # 2. Attempt JSON parsing
        try:
            data = json.loads(decoded_text)
        except json.JSONDecodeError as err:
            result.is_valid = False
            result.parsing_warnings.append(f"Malformed JSON payload: {err}")
            return result

        if not isinstance(data, dict):
            result.is_valid = False
            result.parsing_warnings.append(f"Root JSON is not an object/dict (got {type(data).__name__})")
            return result

        # 3. Extract top-level envelope fields
        result.header_mac = data.get("headerMac")
        result.telemetry_timestamp_raw = data.get("timestamp")

        inner_payload = data.get("payload")
        if not isinstance(inner_payload, dict):
            result.is_valid = False
            result.parsing_warnings.append("Missing or non-dict 'payload' wrapper in outer JSON")
            return result

        result.function_id = inner_payload.get("functionId")
        result.endpoint_id = inner_payload.get("endpointId")
        result.application_id = inner_payload.get("applicationId")

        function_payload = inner_payload.get("functionPayload")
        if not isinstance(function_payload, list):
            result.is_valid = False
            result.parsing_warnings.append("Missing or non-list 'functionPayload' in payload wrapper")
            return result

        # 4. Iterate name-value pairs, tracking repeated phases and MPPTs
        phase_map: dict[int, PhaseRecord] = {}
        mppt_map: dict[int, MPPTRecord] = {}
        current_phase_num: Optional[int] = None
        current_mppt_num: Optional[int] = None

        for idx, entry in enumerate(function_payload):
            if not isinstance(entry, dict) or "name" not in entry:
                result.parsing_warnings.append(f"Entry #{idx} in functionPayload is not a valid name/value dict")
                continue

            result.raw_entries.append(entry)
            raw_name = entry.get("name")
            val = entry.get("value")

            # Validate non-finite floats
            if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                result.parsing_warnings.append(f"Field '{raw_name}' has non-finite value '{val}'")
                val = None

            # Track phase sequence
            if raw_name == "phaseNumber":
                try:
                    current_phase_num = int(val)
                    if current_phase_num not in phase_map:
                        phase_map[current_phase_num] = PhaseRecord(phase_number=current_phase_num)
                except (ValueError, TypeError):
                    result.parsing_warnings.append(f"Invalid phaseNumber value: '{val}'")
                continue

            # Track phase voltage/current by index (e.g. phaseVoltage1 or following phaseNumber)
            if raw_name.startswith("phaseVoltage"):
                p_idx_str = raw_name.replace("phaseVoltage", "")
                p_idx = int(p_idx_str) if p_idx_str.isdigit() else current_phase_num or 1
                if p_idx not in phase_map:
                    phase_map[p_idx] = PhaseRecord(phase_number=p_idx)
                phase_map[p_idx].voltage_raw = val
                phase_map[p_idx].provenance = f"functionPayload[{raw_name}]"
                continue

            if raw_name.startswith("phaseCurrent"):
                p_idx_str = raw_name.replace("phaseCurrent", "")
                p_idx = int(p_idx_str) if p_idx_str.isdigit() else current_phase_num or 1
                if p_idx not in phase_map:
                    phase_map[p_idx] = PhaseRecord(phase_number=p_idx)
                phase_map[p_idx].current_raw = val
                phase_map[p_idx].provenance = f"functionPayload[{raw_name}]"
                continue

            # Track MPPT sequence
            if raw_name == "mpptNumber":
                try:
                    current_mppt_num = int(val)
                    if current_mppt_num not in mppt_map:
                        mppt_map[current_mppt_num] = MPPTRecord(mppt_number=current_mppt_num)
                except (ValueError, TypeError):
                    result.parsing_warnings.append(f"Invalid mpptNumber value: '{val}'")
                continue

            if raw_name.startswith("mpptVoltage"):
                m_idx_str = raw_name.replace("mpptVoltage", "")
                m_idx = int(m_idx_str) if m_idx_str.isdigit() else current_mppt_num or 1
                if m_idx not in mppt_map:
                    mppt_map[m_idx] = MPPTRecord(mppt_number=m_idx)
                mppt_map[m_idx].voltage_raw = val
                mppt_map[m_idx].provenance = f"functionPayload[{raw_name}]"
                continue

            if raw_name.startswith("mpptCurrent"):
                m_idx_str = raw_name.replace("mpptCurrent", "")
                m_idx = int(m_idx_str) if m_idx_str.isdigit() else current_mppt_num or 1
                if m_idx not in mppt_map:
                    mppt_map[m_idx] = MPPTRecord(mppt_number=m_idx)
                mppt_map[m_idx].current_raw = val
                mppt_map[m_idx].provenance = f"functionPayload[{raw_name}]"
                continue

            if raw_name.startswith("mpptPower"):
                m_idx_str = raw_name.replace("mpptPower", "")
                m_idx = int(m_idx_str) if m_idx_str.isdigit() else current_mppt_num or 1
                if m_idx not in mppt_map:
                    mppt_map[m_idx] = MPPTRecord(mppt_number=m_idx)
                mppt_map[m_idx].power_raw = val
                mppt_map[m_idx].provenance = f"functionPayload[{raw_name}]"
                continue

            # Standard scalar field
            meta = get_field_metadata(raw_name)
            if meta:
                # If field already exists (e.g. repeated slaveId), store in scalar and record note
                if meta.normalized_name in result.scalars:
                    result.parsing_warnings.append(f"Field '{raw_name}' appeared multiple times; using latest value.")
                result.scalars[meta.normalized_name] = val
            else:
                result.unknown_fields.append({"name": raw_name, "value": val})
                result.parsing_warnings.append(f"Unrecognized field in payload: '{raw_name}'")

        # Compile ordered phase and MPPT records
        result.phases = [phase_map[k] for k in sorted(phase_map.keys())]
        result.mppts = [mppt_map[k] for k in sorted(mppt_map.keys())]

        return result
