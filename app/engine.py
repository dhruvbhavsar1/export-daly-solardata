"""Phase 3 Orchestration Engine.

Coordinates telemetry parsing, sample validation, and formula execution
on buffered MQTT collection results.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

from app.field_mappings import FIELD_CATALOG, VerificationStatus
from app.formulas import FormulaResult, SiteConfig, SolarFormulaEngine
from app.models import CollectionResult
from app.parser import TelemetryParser
from app.validator import TelemetryValidator, ValidatedSample, ValidationReport


@dataclass
class Phase3Result:
    """Consolidated outcome of Phase 3 parsing, validation, and formula calculations."""
    collection_status: str
    broker: str
    port: int
    topic: str
    logger_serial: str
    total_messages: int
    valid_samples: int
    invalid_samples: int
    duplicate_samples: int
    out_of_order_samples: int
    counter_resets: int
    first_receipt_time: Optional[datetime.datetime]
    last_receipt_time: Optional[datetime.datetime]
    samples: list[ValidatedSample]
    formula_results: dict[str, FormulaResult]
    calculated_formulas: list[str]
    na_formulas: list[str]
    unverified_fields: list[str]
    all_warnings: list[str] = field(default_factory=list)

    def safe_summary(self) -> dict[str, Any]:
        """Provides non-sensitive diagnostic summary of the Phase 3 pipeline."""
        return {
            "collection_status": self.collection_status,
            "broker": self.broker,
            "port": self.port,
            "topic": self.topic,
            "logger_serial": self.logger_serial,
            "total_messages": self.total_messages,
            "valid_samples": self.valid_samples,
            "invalid_samples": self.invalid_samples,
            "duplicate_samples": self.duplicate_samples,
            "out_of_order_samples": self.out_of_order_samples,
            "counter_resets": self.counter_resets,
            "first_receipt_time": self.first_receipt_time.isoformat() if self.first_receipt_time else None,
            "last_receipt_time": self.last_receipt_time.isoformat() if self.last_receipt_time else None,
            "calculated_formulas": self.calculated_formulas,
            "na_formulas": self.na_formulas,
            "unverified_fields": self.unverified_fields,
            "warnings_count": len(self.all_warnings),
        }


class Phase3Engine:
    """Orchestrates parsing, validation, and formulas on collection results."""

    def __init__(self, site_config: Optional[SiteConfig] = None) -> None:
        self.site_config = site_config or SiteConfig()
        self.formula_engine = SolarFormulaEngine(site_config=self.site_config)

    def process_collection(
        self,
        collection: CollectionResult,
        reference_time: Optional[datetime.datetime] = None,
    ) -> Phase3Result:
        """Processes all messages in a CollectionResult through Phase 3 pipeline."""
        now_dt = reference_time or datetime.datetime.now(datetime.timezone.utc)

        # Step 1: Parse all buffered messages
        parsed_samples = [
            TelemetryParser.parse_buffered_message(msg)
            for msg in collection.messages
        ]

        # Step 2: Validate sample series
        validator = TelemetryValidator()
        validation_report: ValidationReport = validator.validate_series(parsed_samples)

        # Step 3: Run formula engine
        formula_map = self.formula_engine.evaluate_all(
            validation_report.samples,
            reference_time=now_dt,
        )

        calculated_list = [
            fid for fid, res in formula_map.items()
            if res.status in ("CALCULATED", "PARTIAL")
        ]
        na_list = [
            fid for fid, res in formula_map.items()
            if res.status == "N/A"
        ]

        # Collect unverified field mappings
        unverified = [
            meta.raw_name for meta in FIELD_CATALOG.values()
            if meta.status == VerificationStatus.UNVERIFIED
        ]

        # Aggregate warnings
        all_warnings: list[str] = list(validation_report.global_warnings)
        for s in validation_report.samples:
            all_warnings.extend(s.validation_warnings)

        first_rcv = collection.messages[0].received_at if collection.messages else None
        last_rcv = collection.messages[-1].received_at if collection.messages else None

        return Phase3Result(
            collection_status=collection.status.value,
            broker=collection.broker,
            port=collection.port,
            topic=collection.topic,
            logger_serial=collection.logger_serial,
            total_messages=len(collection.messages),
            valid_samples=validation_report.valid_samples,
            invalid_samples=validation_report.invalid_samples,
            duplicate_samples=validation_report.duplicate_samples,
            out_of_order_samples=validation_report.out_of_order_samples,
            counter_resets=validation_report.counter_resets,
            first_receipt_time=first_rcv,
            last_receipt_time=last_rcv,
            samples=validation_report.samples,
            formula_results=formula_map,
            calculated_formulas=calculated_list,
            na_formulas=na_list,
            unverified_fields=unverified,
            all_warnings=all_warnings,
        )


def print_phase3_report(result: Phase3Result) -> None:
    """Displays human-readable summary of Phase 3 parsing and calculations."""
    print("\n" + "=" * 70)
    print("PHASE 3: TELEMETRY PARSING, VALIDATION & FORMULA ENGINE REPORT")
    print("=" * 70)
    print(f"Collection Status      : {result.collection_status}")
    print(f"Total Messages         : {result.total_messages}")
    print(f"Valid Samples          : {result.valid_samples}")
    print(f"Invalid / Malformed    : {result.invalid_samples}")
    print(f"Duplicates Detected    : {result.duplicate_samples}")
    print(f"Out of Order Samples   : {result.out_of_order_samples}")
    print(f"Counter Resets / Drops : {result.counter_resets}")
    print("-" * 70)
    print("FORMULA EVALUATION SUMMARY (F01–F30):")
    print(f"Calculated / Partial   : {len(result.calculated_formulas)} formulas -> {', '.join(result.calculated_formulas)}")
    print(f"N/A (Missing Inputs)   : {len(result.na_formulas)} formulas")

    print("\n[KEY CALCULATED FORMULAS]")
    for fid in sorted(result.calculated_formulas):
        f = result.formula_results[fid]
        unit_str = f" {f.unit}" if f.unit else ""
        print(f"  [{f.formula_id}] {f.name:32s}: {f.value}{unit_str} ({f.status})")

    print("\n[UNAVAILABLE FORMULAS REQUIRING EXTERNAL INPUTS (SAMPLE)]")
    for fid in sorted(result.na_formulas)[:6]:
        f = result.formula_results[fid]
        print(f"  [{f.formula_id}] {f.name:32s}: N/A (Missing: {', '.join(f.missing_inputs)})")
    if len(result.na_formulas) > 6:
        print(f"  ... and {len(result.na_formulas) - 6} other formulas requiring smart meter/tariffs/POA sensors.")

    print("\n[UNVERIFIED FIELD CATALOG MAPPINGS]")
    for uf in result.unverified_fields[:8]:
        meta = FIELD_CATALOG.get(uf)
        note = f" (Evidence needed: {meta.evidence_needed})" if meta and meta.evidence_needed else ""
        print(f"  - {uf}: unverified unit/scale{note}")
    if len(result.unverified_fields) > 8:
        print(f"  ... and {len(result.unverified_fields) - 8} additional unverified fields.")

    print("=" * 70 + "\n")


def parse_args() -> Any:
    import argparse
    parser = argparse.ArgumentParser(
        description="Phase 3: Parse, validate, and compute solar metrics on collected MQTT telemetry."
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=1200,
        help="Collection window in seconds (default: 1200s / 20 min).",
    )
    parser.add_argument(
        "--env-file",
        "-e",
        type=str,
        default=None,
        help="Path to custom .env file.",
    )
    parser.add_argument(
        "--dc-capacity",
        type=float,
        default=None,
        help="Site DC nameplate capacity in kWp (e.g. 5.0).",
    )
    parser.add_argument(
        "--psh",
        type=float,
        default=None,
        help="Peak Sun Hours (h/day) for benchmark calculations (e.g. 4.5).",
    )
    parser.add_argument(
        "--tariff",
        type=float,
        default=None,
        help="Electricity contract tariff in INR/kWh (e.g. 8.0).",
    )
    parser.add_argument(
        "--verify-energy-unit",
        action="store_true",
        default=False,
        help="Flag asserting that totalEnergy has been verified in kWh for F01/F02 calculations.",
    )
    parser.add_argument(
        "--pdf",
        action="store_true",
        default=False,
        help="Generate a professional client-ready PDF report.",
    )
    parser.add_argument(
        "--pdf-output-dir",
        type=str,
        default="reports",
        help="Directory to save generated PDF reports (default: reports/).",
    )
    parser.add_argument(
        "--site-name",
        type=str,
        default=None,
        help="Site or client installation name for the PDF report.",
    )
    return parser.parse_args()


def main() -> int:
    import sys
    from app.config import ConfigError, load_config
    from app.collector import MQTTDataCollector

    args = parse_args()

    try:
        config = load_config(env_path=args.env_file)
    except ConfigError as err:
        print(f"[FATAL CONFIG ERROR] {err}", file=sys.stderr)
        return 1

    site_cfg = SiteConfig(
        dc_capacity_kwp=args.dc_capacity,
        psh_hours_per_day=args.psh,
        tariff_inr_per_kwh=args.tariff,
        energy_unit_verified=args.verify_energy_unit,
        energy_unit="kWh" if args.verify_energy_unit else "unverified_units",
    )

    collector = MQTTDataCollector(config=config, duration=args.duration)
    collection_result = collector.collect()

    engine = Phase3Engine(site_config=site_cfg)
    phase3_res = engine.process_collection(collection_result)
    print_phase3_report(phase3_res)

    if args.pdf:
        from app.pdf_report import SolarDailyReportGenerator
        pdf_gen = SolarDailyReportGenerator(
            output_dir=args.pdf_output_dir,
            site_name=args.site_name,
        )
        pdf_path = pdf_gen.generate(phase3_res, site_config=site_cfg)
        print(f"[SUCCESS] PDF report generated at: {pdf_path}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
