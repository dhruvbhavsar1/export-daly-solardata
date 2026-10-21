"""Solar Formula Engine (F01 - F30).

Implements generation, performance, operational, and financial solar formulas
based on Solar_Data_Logger_Formula_Profile(1).pdf.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any, Optional

from app.validator import ValidatedSample


@dataclass
class SiteConfig:
    """Optional site engineering parameters, tariffs, and environmental factors."""
    dc_capacity_kwp: Optional[float] = None
    psh_hours_per_day: Optional[float] = None
    benchmark_factor: float = 1.0
    tariff_inr_per_kwh: Optional[float] = None
    capex_inr: Optional[float] = None
    annual_om_cost_inr: Optional[float] = None
    grid_emission_factor_kg_per_kwh: Optional[float] = None
    tree_co2_kg_per_tree_year: Optional[float] = None
    expected_cadence_seconds: Optional[int] = None
    timezone_name: str = "Asia/Kolkata"
    online_timeout_seconds: int = 900
    energy_unit_verified: bool = False
    energy_unit: str = "kWh"
    energy_scale_factor: float = 1.0


@dataclass
class FormulaResult:
    """Structured outcome for an individual formula evaluation."""
    formula_id: str
    name: str
    status: str  # "CALCULATED", "PARTIAL", "N/A"
    value: Optional[Any]
    unit: Optional[str]
    inputs_used: dict[str, Any] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    explanation: str = ""
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "formula_id": self.formula_id,
            "name": self.name,
            "status": self.status,
            "value": self.value,
            "unit": self.unit,
            "inputs_used": self.inputs_used,
            "missing_inputs": self.missing_inputs,
            "explanation": self.explanation,
            "assumptions": self.assumptions,
        }


class SolarFormulaEngine:
    """
    Evaluates formulas F01 through F30 against validated telemetry samples.

    Strictly adheres to documented evidence: formulas missing inputs return structured N/A results.
    """

    def __init__(self, site_config: Optional[SiteConfig] = None) -> None:
        self.site_config = site_config or SiteConfig()

    def evaluate_all(
        self,
        samples: list[ValidatedSample],
        reference_time: Optional[datetime.datetime] = None,
    ) -> dict[str, FormulaResult]:
        """Evaluates all formulas F01–F30 and returns results mapped by formula ID."""
        now_dt = reference_time or datetime.datetime.now(datetime.timezone.utc)
        usable_samples = [s for s in samples if s.is_usable and not s.is_duplicate]

        results: dict[str, FormulaResult] = {}

        # F01: Interval generated energy
        results["F01"] = self._eval_f01(usable_samples)

        # F02: Period energy
        results["F02"] = self._eval_f02(results["F01"], usable_samples)

        # F03: Expected generation (simple benchmark)
        results["F03"] = self._eval_f03()

        # F04: Generation achievement (%)
        results["F04"] = self._eval_f04(results["F02"], results["F03"])

        # F05: IEC-style performance ratio (PR)
        results["F05"] = self._eval_f05()

        # F06: Specific yield
        results["F06"] = self._eval_f06(results["F02"])

        # F07: Capacity utilization factor (CUF)
        results["F07"] = self._eval_f07(results["F02"])

        # F08: Peak day and average daily generation
        results["F08"] = self._eval_f08()

        # F09: Month-on-month / year-on-year change
        results["F09"] = self._eval_f09()

        # F10: Weather-normalized generation
        results["F10"] = self._eval_f10()

        # F11: Self-consumed solar energy
        results["F11"] = self._eval_f11()

        # F12: Total load consumption
        results["F12"] = self._eval_f12()

        # F13: Net grid energy
        results["F13"] = self._eval_f13()

        # F14: Self-consumption ratio
        results["F14"] = self._eval_f14()

        # F15: Solar self-sufficiency
        results["F15"] = self._eval_f15()

        # F16: Financial benefit (accurate model)
        results["F16"] = self._eval_f16()

        # F17: Simple gross generation value
        results["F17"] = self._eval_f17(results["F02"])

        # F18: Annual savings forecast
        results["F18"] = self._eval_f18()

        # F19: Simple payback
        results["F19"] = self._eval_f19()

        # F20: Simple ROI / lifetime return
        results["F20"] = self._eval_f20()

        # F21: Avoided CO2 (estimate)
        results["F21"] = self._eval_f21(results["F02"])

        # F22: Tree equivalent
        results["F22"] = self._eval_f22(results["F21"])

        # F23: Monthly PDF headline
        results["F23"] = self._eval_f23()

        # F24: Online / offline status
        results["F24"] = self._eval_f24(samples, now_dt)

        # F25: Inverter temperature
        results["F25"] = self._eval_f25(usable_samples)

        # F26: Panel soiling / cleaning alert
        results["F26"] = self._eval_f26()

        # F27: Wind warning
        results["F27"] = self._eval_f27()

        # F28: Faults / wiring / grid status
        results["F28"] = self._eval_f28(usable_samples)

        # F29: Data coverage and freshness
        results["F29"] = self._eval_f29(samples, now_dt)

        # F30: Weather-based low-output detection
        results["F30"] = self._eval_f30()

        return results

    # -------------------------------------------------------------------------
    # Core Formulas
    # -------------------------------------------------------------------------

    def _eval_f01(self, usable_samples: list[ValidatedSample]) -> FormulaResult:
        """
        F01: Interval generated energy.
        E_interval = max(0, E_total_end - E_total_start)
        Only calculated when cumulative energy counter units are verified.
        """
        cfg = self.site_config
        if not cfg.energy_unit_verified:
            return FormulaResult(
                formula_id="F01",
                name="Interval generated energy",
                status="N/A",
                value=None,
                unit="unverified_cumulative_units",
                inputs_used={"sample_count": len(usable_samples)},
                missing_inputs=["Verified engineering unit for totalEnergy (e.g. kWh vs Wh vs 0.1kWh)"],
                explanation=(
                    "Cumulative energy counter (totalEnergy) unit and scaling are unverified. "
                    "Cannot calculate physical interval energy without confirmed unit scaling."
                ),
                assumptions=["Counter unit remains in unverified raw units until vendor spec is confirmed."],
            )

        if len(usable_samples) < 2:
            return FormulaResult(
                formula_id="F01",
                name="Interval generated energy",
                status="N/A",
                value=None,
                unit=cfg.energy_unit,
                inputs_used={"sample_count": len(usable_samples)},
                missing_inputs=[">= 2 consecutive valid samples with totalEnergy"],
                explanation="Need at least two consecutive valid telemetry readings to compute counter delta.",
            )

        intervals: list[dict[str, Any]] = []
        total_delta = 0.0
        reset_count = 0
        valid_intervals = 0

        for i in range(1, len(usable_samples)):
            s_prev = usable_samples[i - 1]
            s_curr = usable_samples[i]

            prev_val = s_prev.telemetry.get_scalar("total_energy_raw")
            curr_val = s_curr.telemetry.get_scalar("total_energy_raw")

            if prev_val is None or curr_val is None:
                continue

            try:
                p_float = float(prev_val)
                c_float = float(curr_val)
            except (ValueError, TypeError):
                continue

            # Counter reset / negative delta check:
            # Negative delta must not silently be treated as valid zero energy.
            # Flag interval as a reset and exclude from valid generation accumulation.
            if c_float < p_float:
                reset_flag = True
                reset_count += 1
                intervals.append({
                    "from_index": s_prev.telemetry.message_index,
                    "to_index": s_curr.telemetry.message_index,
                    "delta": None,
                    "counter_reset": True,
                    "is_valid_interval": False,
                    "warning": f"Counter drop detected from {p_float} to {c_float}; excluded as unverified reset/rollover.",
                })
            else:
                delta = (c_float - p_float) * cfg.energy_scale_factor
                reset_flag = False
                total_delta += delta
                valid_intervals += 1
                intervals.append({
                    "from_index": s_prev.telemetry.message_index,
                    "to_index": s_curr.telemetry.message_index,
                    "delta": delta,
                    "counter_reset": False,
                    "is_valid_interval": True,
                })

        explanation_parts = [
            f"Calculated from {valid_intervals} valid cumulative totalEnergy interval(s) out of {len(intervals)} total."
        ]
        if reset_count > 0:
            explanation_parts.append(
                f"{reset_count} interval(s) had negative counter deltas and were flagged and excluded from "
                "accumulation as unverified reset/rollovers without fabricating rollover modulus."
            )

        return FormulaResult(
            formula_id="F01",
            name="Interval generated energy",
            status="CALCULATED" if valid_intervals > 0 else "N/A",
            value=round(total_delta, 4) if valid_intervals > 0 else None,
            unit=cfg.energy_unit,
            inputs_used={
                "intervals": intervals,
                "interval_count": len(intervals),
                "valid_intervals": valid_intervals,
                "reset_intervals": reset_count,
            },
            explanation=" ".join(explanation_parts),
            assumptions=[
                f"Counter verified as {cfg.energy_unit} (scale factor {cfg.energy_scale_factor}).",
                *(["Counter reset intervals excluded from generation."] if reset_count > 0 else []),
            ],
        )

    def _eval_f02(self, f01_res: FormulaResult, usable_samples: list[ValidatedSample]) -> FormulaResult:
        """
        F02: Period energy.
        E_period = sum(valid interval generation within local period)
        """
        cfg = self.site_config
        if not cfg.energy_unit_verified:
            return FormulaResult(
                formula_id="F02",
                name="Period energy",
                status="N/A",
                value=None,
                unit="unverified_cumulative_units",
                missing_inputs=["Verified engineering unit for energy counters"],
                explanation="Cannot calculate period energy without verified energy units.",
            )

        if f01_res.status != "CALCULATED" or f01_res.value is None:
            return FormulaResult(
                formula_id="F02",
                name="Period energy",
                status="N/A",
                value=None,
                unit=cfg.energy_unit,
                missing_inputs=["F01 valid interval energy"],
                explanation="No valid interval delta available within collection period.",
            )

        return FormulaResult(
            formula_id="F02",
            name="Period energy",
            status="PARTIAL",
            value=f01_res.value,
            unit=f01_res.unit,
            inputs_used={"interval_sum": f01_res.value},
            explanation=(
                "Total energy collected over the current collection window. "
                "Explicitly labeled as collection-window partial generation so far, "
                "NOT as a complete 24-hour daily or monthly total."
            ),
            assumptions=["Collection window covers only the observed timeframe."],
        )

    def _eval_f03(self) -> FormulaResult:
        """F03: Expected generation (simple benchmark)."""
        cfg = self.site_config
        if cfg.dc_capacity_kwp is None or cfg.psh_hours_per_day is None:
            return FormulaResult(
                formula_id="F03",
                name="Expected generation (simple benchmark)",
                status="N/A",
                value=None,
                unit="kWh",
                missing_inputs=["dc_capacity_kwp", "psh_hours_per_day"],
                explanation="Requires PV DC nameplate capacity (kWp) and Peak Sun Hours (h/day).",
            )
        expected = cfg.dc_capacity_kwp * cfg.psh_hours_per_day * 1.0 * cfg.benchmark_factor
        return FormulaResult(
            formula_id="F03",
            name="Expected generation (simple benchmark)",
            status="CALCULATED",
            value=round(expected, 2),
            unit="kWh/day",
            inputs_used={"dc_capacity_kwp": cfg.dc_capacity_kwp, "psh": cfg.psh_hours_per_day},
            explanation=f"{cfg.dc_capacity_kwp} kWp * {cfg.psh_hours_per_day} PSH * {cfg.benchmark_factor} factor.",
            assumptions=["Theoretical irradiation-based benchmark; excludes weather/shading loss factors."],
        )

    def _eval_f04(self, f02: FormulaResult, f03: FormulaResult) -> FormulaResult:
        """F04: Generation achievement (%)."""
        if f02.value is None or f03.value is None or f03.value <= 0:
            return FormulaResult(
                formula_id="F04",
                name="Generation achievement (%)",
                status="N/A",
                value=None,
                unit="%",
                missing_inputs=["F02 period energy", "F03 expected generation"],
                explanation="Requires both actual generation (F02) and non-zero expected benchmark (F03).",
            )
        achievement = (float(f02.value) / float(f03.value)) * 100.0
        return FormulaResult(
            formula_id="F04",
            name="Generation achievement (%)",
            status="CALCULATED",
            value=round(achievement, 2),
            unit="%",
            inputs_used={"actual": f02.value, "expected": f03.value},
            explanation=f"({f02.value} / {f03.value}) * 100",
            assumptions=["Only valid when actual and expected energy share identical engineering units."],
        )

    def _eval_f05(self) -> FormulaResult:
        """F05: IEC-style performance ratio (PR)."""
        return FormulaResult(
            formula_id="F05",
            name="IEC-style performance ratio (PR)",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Plane-of-array irradiation (H_POA in kWh/m²)", "Reference irradiance (G_STC)", "P_DC (kWp)"],
            explanation="Requires on-site plane-of-array pyranometer/sensor data and DC capacity. Cannot use simple benchmark.",
        )

    def _eval_f06(self, f02: FormulaResult) -> FormulaResult:
        """F06: Specific yield (kWh/kWp)."""
        cfg = self.site_config
        if cfg.dc_capacity_kwp is None or f02.value is None:
            return FormulaResult(
                formula_id="F06",
                name="Specific yield",
                status="N/A",
                value=None,
                unit="kWh/kWp",
                missing_inputs=["dc_capacity_kwp", "F02 generation"],
                explanation="Requires PV DC capacity (kWp) and verified AC generation.",
            )
        yield_val = float(f02.value) / cfg.dc_capacity_kwp
        return FormulaResult(
            formula_id="F06",
            name="Specific yield",
            status="CALCULATED",
            value=round(yield_val, 2),
            unit="kWh/kWp",
            inputs_used={"energy": f02.value, "dc_capacity_kwp": cfg.dc_capacity_kwp},
            explanation=f"{f02.value} / {cfg.dc_capacity_kwp}",
        )

    def _eval_f07(self, f02: FormulaResult) -> FormulaResult:
        """F07: Capacity utilization factor (CUF)."""
        cfg = self.site_config
        if cfg.dc_capacity_kwp is None or f02.value is None:
            return FormulaResult(
                formula_id="F07",
                name="Capacity utilization factor (CUF)",
                status="N/A",
                value=None,
                unit="%",
                missing_inputs=["dc_capacity_kwp", "calendar period hours"],
                explanation="Requires DC nameplate capacity and complete calendar hours of reporting period.",
            )
        return FormulaResult(
            formula_id="F07",
            name="Capacity utilization factor (CUF)",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Full calendar month/day hours (incomplete window)"],
            explanation="CUF cannot be calculated for an incomplete sub-hourly collection window.",
        )

    def _eval_f08(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F08",
            name="Peak day and average daily generation",
            status="N/A",
            value=None,
            unit="kWh/day",
            missing_inputs=["Multi-day historical complete generation series"],
            explanation="Requires historical full-day generation records; cannot treat partial days as full days.",
        )

    def _eval_f09(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F09",
            name="Month-on-month / year-on-year change",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Historical matching period baseline"],
            explanation="Requires matching elapsed days or complete previous month/year baseline data.",
        )

    def _eval_f10(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F10",
            name="Weather-normalized generation",
            status="N/A",
            value=None,
            unit="kWh",
            missing_inputs=["H_POA plane-of-array irradiance sensor feed"],
            explanation="Requires on-site solar irradiance measurements to normalize for cloud cover.",
        )

    def _eval_f11(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F11",
            name="Self-consumed solar energy",
            status="N/A",
            value=None,
            unit="kWh",
            missing_inputs=["Bidirectional smart-meter solar export register"],
            explanation="Requires smart-meter grid export to distinguish self-consumed from exported solar energy.",
        )

    def _eval_f12(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F12",
            name="Total load consumption",
            status="N/A",
            value=None,
            unit="kWh",
            missing_inputs=["Bidirectional smart-meter grid import and export registers"],
            explanation="Requires building grid import and solar export meters.",
        )

    def _eval_f13(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F13",
            name="Net grid energy",
            status="N/A",
            value=None,
            unit="kWh",
            missing_inputs=["Bidirectional smart-meter grid import and export registers"],
            explanation="Requires separate grid import and grid export cumulative registers.",
        )

    def _eval_f14(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F14",
            name="Self-consumption ratio",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Self-consumed solar energy (F11)"],
            explanation="Requires bidirectional smart-meter export data.",
        )

    def _eval_f15(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F15",
            name="Solar self-sufficiency",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Total load consumption (F12)"],
            explanation="Requires whole-facility load consumption telemetry.",
        )

    def _eval_f16(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F16",
            name="Financial benefit (accurate model)",
            status="N/A",
            value=None,
            unit="INR",
            missing_inputs=["Time-of-use tariffs", "Export credit rate", "Fixed/demand charges"],
            explanation="Requires utility contract structure, TOU rates, and smart-meter net metering rules.",
        )

    def _eval_f17(self, f02: FormulaResult) -> FormulaResult:
        """F17: Simple gross generation value."""
        cfg = self.site_config
        if cfg.tariff_inr_per_kwh is None or f02.value is None:
            return FormulaResult(
                formula_id="F17",
                name="Simple gross generation value",
                status="N/A",
                value=None,
                unit="INR",
                missing_inputs=["tariff_inr_per_kwh", "verified F02 energy in kWh"],
                explanation="Requires configured contract tariff (₹/kWh) and verified kWh generation.",
            )
        gross = float(f02.value) * cfg.tariff_inr_per_kwh
        return FormulaResult(
            formula_id="F17",
            name="Simple gross generation value",
            status="CALCULATED",
            value=round(gross, 2),
            unit="INR",
            inputs_used={"energy": f02.value, "tariff": cfg.tariff_inr_per_kwh},
            explanation=f"{f02.value} kWh * ₹{cfg.tariff_inr_per_kwh}/kWh",
            assumptions=["Indicative gross generation value only; not actual bill savings without export metering."],
        )

    def _eval_f18(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F18",
            name="Annual savings forecast",
            status="N/A",
            value=None,
            unit="INR/year",
            missing_inputs=["12-month generation and seasonal insolation model"],
            explanation="Cannot extrapolate single short collection window into an annual forecast.",
        )

    def _eval_f19(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F19",
            name="Simple payback",
            status="N/A",
            value=None,
            unit="years",
            missing_inputs=["Net project capex", "Annual net cash savings after O&M"],
            explanation="Requires verified capital expenditure and multi-year net operational cashflows.",
        )

    def _eval_f20(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F20",
            name="Simple ROI / lifetime return",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Capex", "Discount rate", "Degradation factor", "Lifetime cashflows"],
            explanation="Requires comprehensive financial investment parameters for NPV/IRR.",
        )

    def _eval_f21(self, f02: FormulaResult) -> FormulaResult:
        """F21: Avoided CO2 (estimate)."""
        cfg = self.site_config
        if cfg.grid_emission_factor_kg_per_kwh is None or f02.value is None:
            return FormulaResult(
                formula_id="F21",
                name="Avoided CO2 (estimate)",
                status="N/A",
                value=None,
                unit="kg CO2",
                missing_inputs=["grid_emission_factor_kg_per_kwh", "verified F02 generation"],
                explanation="Requires region- and year-specific grid emission factor (e.g. 0.82 kg CO2/kWh).",
            )
        co2 = float(f02.value) * cfg.grid_emission_factor_kg_per_kwh
        return FormulaResult(
            formula_id="F21",
            name="Avoided CO2 (estimate)",
            status="CALCULATED",
            value=round(co2, 3),
            unit="kg CO2",
            inputs_used={"energy": f02.value, "factor": cfg.grid_emission_factor_kg_per_kwh},
            explanation=f"{f02.value} kWh * {cfg.grid_emission_factor_kg_per_kwh} kg CO2/kWh",
            assumptions=["Estimated avoided grid emissions; not direct measurement."],
        )

    def _eval_f22(self, f21: FormulaResult) -> FormulaResult:
        """F22: Tree equivalent."""
        cfg = self.site_config
        if cfg.tree_co2_kg_per_tree_year is None or f21.value is None:
            return FormulaResult(
                formula_id="F22",
                name="Tree equivalent",
                status="N/A",
                value=None,
                unit="trees",
                missing_inputs=["tree_co2_kg_per_tree_year factor", "F21 avoided CO2"],
                explanation="Requires assumed annual CO2 sequestration factor per tree (e.g. 22.55 kg/tree/year).",
            )
        trees = float(f21.value) / cfg.tree_co2_kg_per_tree_year
        return FormulaResult(
            formula_id="F22",
            name="Tree equivalent",
            status="CALCULATED",
            value=round(trees, 2),
            unit="trees",
            inputs_used={"co2_kg": f21.value, "tree_factor": cfg.tree_co2_kg_per_tree_year},
            explanation=f"{f21.value} kg CO2 / {cfg.tree_co2_kg_per_tree_year} kg/tree",
            assumptions=["Illustrative communication-only metric with explicitly declared tree factor."],
        )

    def _eval_f23(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F23",
            name="Monthly PDF headline",
            status="N/A",
            value=None,
            unit=None,
            missing_inputs=["Phase 4 reporting pipeline and full monthly billing cycle"],
            explanation="Monthly headline is generated in Phase 4 reporting.",
        )

    def _eval_f24(self, samples: list[ValidatedSample], now_dt: datetime.datetime) -> FormulaResult:
        """F24: Online / offline status."""
        cfg = self.site_config
        if not samples:
            return FormulaResult(
                formula_id="F24",
                name="Online / offline status",
                status="CALCULATED",
                value="OFFLINE (NO_SAMPLES)",
                unit="status",
                inputs_used={"sample_count": 0},
                explanation="No telemetry samples received.",
            )

        latest_sample = samples[-1]
        ts = latest_sample.timestamp_utc or latest_sample.telemetry.received_at_utc
        elapsed_sec = (now_dt - ts).total_seconds()
        is_online = elapsed_sec <= cfg.online_timeout_seconds

        status_val = "ONLINE" if is_online else f"OFFLINE (Heartbeat age: {int(elapsed_sec)}s > {cfg.online_timeout_seconds}s)"

        return FormulaResult(
            formula_id="F24",
            name="Online / offline status",
            status="CALCULATED",
            value=status_val,
            unit="status",
            inputs_used={"last_heartbeat_utc": ts.isoformat(), "elapsed_seconds": round(elapsed_sec, 1)},
            explanation=f"Evaluated against configured timeout of {cfg.online_timeout_seconds}s.",
        )

    def _eval_f25(self, usable_samples: list[ValidatedSample]) -> FormulaResult:
        """F25: Inverter temperature."""
        if not usable_samples:
            return FormulaResult(
                formula_id="F25",
                name="Inverter temperature",
                status="N/A",
                value=None,
                unit="unverified_temp_scale",
                missing_inputs=["inverterTemperature telemetry reading"],
                explanation="No valid temperature reading available.",
            )
        latest = usable_samples[-1]
        raw_temp = latest.telemetry.get_scalar("inverter_temperature_raw")
        if raw_temp is None:
            return FormulaResult(
                formula_id="F25",
                name="Inverter temperature",
                status="N/A",
                value=None,
                unit="unverified_temp_scale",
                missing_inputs=["inverterTemperature field in latest sample"],
                explanation="Field inverterTemperature missing in telemetry payload.",
            )

        return FormulaResult(
            formula_id="F25",
            name="Inverter temperature",
            status="CALCULATED",
            value=raw_temp,
            unit="raw_value (unverified °C / 0.1°C)",
            inputs_used={"raw_reading": raw_temp},
            explanation="Preserves raw telemetry reading without inventing a manufacturer OK threshold.",
            assumptions=["Operating limit not verified; cannot declare status OK without vendor specifications."],
        )

    def _eval_f26(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F26",
            name="Panel soiling / cleaning alert",
            status="N/A",
            value=None,
            unit="%",
            missing_inputs=["Dedicated soiling sensor feed or weather-adjusted string comparison baseline"],
            explanation="Cannot infer soiling loss from power drop alone without persistent multi-day irradiance data.",
        )

    def _eval_f27(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F27",
            name="Wind warning",
            status="N/A",
            value=None,
            unit="m/s",
            missing_inputs=["Local weather API wind gust forecast or onsite anemometer"],
            explanation="Requires gust forecast and site engineering rooftop access limits.",
        )

    def _eval_f28(self, usable_samples: list[ValidatedSample]) -> FormulaResult:
        """F28: Faults / wiring / grid status."""
        if not usable_samples:
            return FormulaResult(
                formula_id="F28",
                name="Faults / wiring / grid status",
                status="N/A",
                value=None,
                unit=None,
                missing_inputs=["Valid inverter status telemetry"],
                explanation="No usable telemetry samples.",
            )
        latest = usable_samples[-1]
        raw_status = latest.telemetry.get_scalar("inverter_status_raw")

        return FormulaResult(
            formula_id="F28",
            name="Faults / wiring / grid status",
            status="CALCULATED",
            value={
                "inverter_status_code": raw_status,
                "alarm_status_label": "No detected wiring alarm (software status only; wiring not verified safe)",
            },
            unit="state_code",
            inputs_used={"inverterStatus": raw_status},
            explanation=(
                "Reports raw inverterStatus state code. Software telemetry alone cannot certify wiring safety."
            ),
            assumptions=["Raw status code mapping requires manufacturer documentation."],
        )

    def _eval_f29(self, samples: list[ValidatedSample], now_dt: datetime.datetime) -> FormulaResult:
        """
        F29: Data coverage and freshness.
        Freshness = now - newest_sample_time.
        Coverage = valid / expected * 100 if cadence configured.
        """
        if not samples:
            return FormulaResult(
                formula_id="F29",
                name="Data coverage and freshness",
                status="CALCULATED",
                value={"coverage_pct": 0.0, "freshness_seconds": None, "message_count": 0},
                unit=None,
                inputs_used={"sample_count": 0},
                explanation="No telemetry received in collection window.",
            )

        valid_count = sum(1 for s in samples if s.is_usable and not s.is_duplicate)
        latest = samples[-1]
        latest_ts = latest.timestamp_utc or latest.telemetry.received_at_utc
        freshness_sec = max(0.0, (now_dt - latest_ts).total_seconds())

        cfg = self.site_config
        if cfg.expected_cadence_seconds and cfg.expected_cadence_seconds > 0:
            first_ts = samples[0].timestamp_utc or samples[0].telemetry.received_at_utc
            time_span_sec = max(1.0, (latest_ts - first_ts).total_seconds())
            expected_samples = max(1, int(time_span_sec / cfg.expected_cadence_seconds) + 1)
            coverage_pct = round(min(100.0, (valid_count / expected_samples) * 100.0), 2)
            cov_val: Optional[float] = coverage_pct
        else:
            # Cadence unknown: report count and observed span, do NOT invent coverage %
            cov_val = None

        first_ts = samples[0].timestamp_utc or samples[0].telemetry.received_at_utc
        observed_span = (latest_ts - first_ts).total_seconds()

        return FormulaResult(
            formula_id="F29",
            name="Data coverage and freshness",
            status="CALCULATED",
            value={
                "freshness_seconds": round(freshness_sec, 1),
                "coverage_pct": cov_val,
                "message_count": len(samples),
                "valid_count": valid_count,
                "observed_time_span_seconds": round(observed_span, 1),
            },
            unit=None,
            inputs_used={
                "samples": len(samples),
                "valid": valid_count,
                "cadence_configured": cfg.expected_cadence_seconds is not None,
            },
            explanation=(
                f"Freshness: {round(freshness_sec, 1)}s. "
                + (f"Coverage: {cov_val}% (based on {cfg.expected_cadence_seconds}s cadence)."
                   if cov_val is not None else
                   "Cadence unknown; reporting message count and observed time span without fabricating percentage.")
            ),
        )

    def _eval_f30(self) -> FormulaResult:
        return FormulaResult(
            formula_id="F30",
            name="Weather-based low-output detection",
            status="N/A",
            value=None,
            unit=None,
            missing_inputs=["Multi-day historical plant generation and weather-adjusted irradiance baseline"],
            explanation="Requires historical generation models and calibrated weather sensor inputs.",
        )
