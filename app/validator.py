"""Validation layer for telemetry timestamps, counter monotonicity, and sample integrity."""

from __future__ import annotations

import datetime
import hashlib
from dataclasses import dataclass, field
from typing import Optional

from app.parser import NormalizedTelemetry


@dataclass
class ValidatedSample:
    """Telemetry sample enriched with validation status, anomaly flags, and warnings."""
    telemetry: NormalizedTelemetry
    is_usable: bool
    is_duplicate: bool = False
    is_out_of_order: bool = False
    has_counter_reset: bool = False
    timestamp_utc: Optional[datetime.datetime] = None
    validation_warnings: list[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Summary of data quality and validation results across a collection run."""
    total_samples: int
    valid_samples: int
    invalid_samples: int
    duplicate_samples: int
    out_of_order_samples: int
    counter_resets: int
    samples: list[ValidatedSample] = field(default_factory=list)
    global_warnings: list[str] = field(default_factory=list)


class TelemetryValidator:
    """
    Validates a sequence of parsed telemetry samples.

    Identifies duplicates, out-of-order records, timestamp anomalies,
    and counter resets without silently discarding any data.
    """

    def __init__(self) -> None:
        self.seen_signatures: set[str] = set()
        self.last_timestamp_num: Optional[float] = None
        self.last_total_energy: Optional[float] = None

    def validate_sample(self, telemetry: NormalizedTelemetry) -> ValidatedSample:
        """Validates an individual telemetry packet against historical context."""
        warnings: list[str] = list(telemetry.parsing_warnings)
        is_usable = telemetry.is_valid
        is_duplicate = False
        is_out_of_order = False
        has_counter_reset = False
        parsed_dt: Optional[datetime.datetime] = None

        # 1. Timestamp validation
        raw_ts = telemetry.telemetry_timestamp_raw
        if raw_ts is None:
            warnings.append("Missing telemetry timestamp in packet envelope.")
            is_usable = False
        else:
            try:
                ts_num = float(raw_ts)
                if ts_num <= 0:
                    warnings.append(f"Non-positive telemetry timestamp: {ts_num}")
                    is_usable = False
                else:
                    # Convert to UTC datetime safely
                    try:
                        parsed_dt = datetime.datetime.fromtimestamp(ts_num, tz=datetime.timezone.utc)
                    except (OverflowError, ValueError) as err:
                        warnings.append(f"Timestamp value {ts_num} exceeds valid date range: {err}")
                        is_usable = False

                    # Check chronological ordering
                    if self.last_timestamp_num is not None:
                        if ts_num < self.last_timestamp_num:
                            is_out_of_order = True
                            warnings.append(
                                f"Out-of-order sample: timestamp decreased from "
                                f"{self.last_timestamp_num} to {ts_num}."
                            )
                    self.last_timestamp_num = ts_num
            except (ValueError, TypeError):
                warnings.append(f"Malformed non-numeric timestamp: '{raw_ts}'")
                is_usable = False

        # 2. Duplicate detection strategy
        # Signature incorporates MAC address, raw timestamp, and payload content hash
        mac = telemetry.header_mac or "UNKNOWN_MAC"
        ts_part = str(raw_ts) if raw_ts is not None else "NO_TS"
        payload_hash = hashlib.sha256(telemetry.raw_bytes).hexdigest()[:16]
        sig = f"{mac}:{ts_part}:{payload_hash}"

        if sig in self.seen_signatures:
            is_duplicate = True
            warnings.append(f"Duplicate message detected (signature: {sig}).")
            is_usable = False
        else:
            self.seen_signatures.add(sig)

        # 3. Cumulative counter monotonicity and reset detection
        curr_total_energy = telemetry.get_scalar("total_energy_raw")
        if curr_total_energy is not None:
            try:
                curr_val = float(curr_total_energy)
                if curr_val < 0:
                    warnings.append(f"Negative cumulative energy counter: {curr_val}")
                    is_usable = False
                elif self.last_total_energy is not None:
                    if curr_val < self.last_total_energy:
                        has_counter_reset = True
                        warnings.append(
                            f"Counter reset or rollover detected: totalEnergy dropped from "
                            f"{self.last_total_energy} to {curr_val}."
                        )
                if curr_val >= 0:
                    self.last_total_energy = curr_val
            except (ValueError, TypeError):
                warnings.append(f"Non-numeric totalEnergy reading: '{curr_total_energy}'")
                is_usable = False

        # 4. Check other non-negative fields
        e_today = telemetry.get_scalar("energy_today_raw")
        if e_today is not None:
            try:
                if float(e_today) < 0:
                    warnings.append(f"Negative eToday reading: {e_today}")
                    is_usable = False
            except (ValueError, TypeError):
                warnings.append(f"Non-numeric eToday reading: '{e_today}'")
                is_usable = False

        return ValidatedSample(
            telemetry=telemetry,
            is_usable=is_usable,
            is_duplicate=is_duplicate,
            is_out_of_order=is_out_of_order,
            has_counter_reset=has_counter_reset,
            timestamp_utc=parsed_dt,
            validation_warnings=warnings,
        )

    def validate_series(self, samples: list[NormalizedTelemetry]) -> ValidationReport:
        """Validates an entire sequence of telemetry samples in arrival order."""
        validated_list: list[ValidatedSample] = []
        dup_count = 0
        ooo_count = 0
        resets_count = 0
        valid_count = 0
        invalid_count = 0

        for sample in samples:
            val_sample = self.validate_sample(sample)
            validated_list.append(val_sample)

            if val_sample.is_duplicate:
                dup_count += 1
            if val_sample.is_out_of_order:
                ooo_count += 1
            if val_sample.has_counter_reset:
                resets_count += 1

            if val_sample.is_usable:
                valid_count += 1
            else:
                invalid_count += 1

        return ValidationReport(
            total_samples=len(samples),
            valid_samples=valid_count,
            invalid_samples=invalid_count,
            duplicate_samples=dup_count,
            out_of_order_samples=ooo_count,
            counter_resets=resets_count,
            samples=validated_list,
        )
