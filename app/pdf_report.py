"""Solar Daily Report PDF Generator.

Produces clean, client-ready solar telemetry and metrics reports in PDF format
using ReportLab and Matplotlib, conforming to the Solar Data Logger Formula Profile.
"""

from __future__ import annotations

import datetime
import io
import os
from pathlib import Path
from typing import Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.engine import Phase3Result
from app.formulas import SiteConfig
from app.validator import ValidatedSample


# -----------------------------------------------------------------------------
# Timezone helper (IST / UTC+05:30)
# -----------------------------------------------------------------------------
IST_TZ = datetime.timezone(datetime.timedelta(hours=5, minutes=30), name="IST")


def to_ist(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """Converts a datetime to Asia/Kolkata (IST), treating naive datetimes as UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(IST_TZ)


def format_ist_str(dt: Optional[datetime.datetime], include_seconds: bool = True) -> str:
    """Formats datetime in Asia/Kolkata timezone with explicit IST designation."""
    if dt is None:
        return "N/A"
    ist_dt = to_ist(dt)
    if include_seconds:
        return ist_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    return ist_dt.strftime("%Y-%m-%d %H:%M IST")


# -----------------------------------------------------------------------------
# Numbered Canvas for "Page X of Y" and Running Header/Footer
# -----------------------------------------------------------------------------
class NumberedCanvas(canvas.Canvas):
    """
    Two-pass canvas that counts total pages and draws running headers and footers
    with exact page numbering ('Page X of Y') and report metadata.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int) -> None:
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#64748b"))

        # Footer rule and text
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(36, 32, A4[0] - 36, 32)

        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(A4[0] - 36, 20, page_str)
        self.drawString(36, 20, "Confidential • Solar Daily Telemetry Report • Automated System")

        self.restoreState()


# -----------------------------------------------------------------------------
# Chart Generation (In-Memory BytesIO, Headless Matplotlib)
# -----------------------------------------------------------------------------
def generate_active_power_chart(
    samples: list[ValidatedSample],
    width_inches: float = 6.8,
    height_inches: float = 2.4,
) -> Optional[io.BytesIO]:
    """
    Renders an in-memory time-series chart of observed active power readings.
    Returns None if fewer than 2 valid points exist.
    """
    valid_points: list[tuple[datetime.datetime, float]] = []

    for s in samples:
        if not s.is_usable or s.is_duplicate:
            continue
        val = s.telemetry.get_scalar("active_power_raw")
        if val is None:
            continue
        try:
            num_val = float(val)
        except (ValueError, TypeError):
            continue

        ts = s.timestamp_utc or s.telemetry.received_at_utc
        ist_ts = to_ist(ts)
        valid_points.append((ist_ts, num_val))

    if len(valid_points) < 2:
        return None

    # Sort chronologically
    valid_points.sort(key=lambda p: p[0])
    times = [p[0] for p in valid_points]
    values = [p[1] for p in valid_points]

    fig, ax = plt.subplots(figsize=(width_inches, height_inches), dpi=150)
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8fafc")

    ax.plot(
        times,
        values,
        color="#0284c7",
        linewidth=1.8,
        marker="o",
        markersize=4,
        markerfacecolor="#0369a1",
        label="Active Power (Raw Telemetry)",
    )

    ax.set_title("Active Power Telemetry (Observed Collection Window)", fontsize=9, fontweight="bold", color="#1e293b", pad=8)
    ax.set_xlabel("Time (Asia/Kolkata IST)", fontsize=7.5, color="#475569")
    ax.set_ylabel("Reported Value (Unverified Scale)", fontsize=7.5, color="#475569")

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S", tz=IST_TZ))
    fig.autofmt_xdate(rotation=0, ha="center")

    ax.grid(True, linestyle="--", alpha=0.5, color="#cbd5e1")
    ax.tick_params(axis="both", which="major", labelsize=7, colors="#475569")

    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
        spine.set_linewidth(0.8)

    ax.legend(loc="upper right", fontsize=7, framealpha=0.9, facecolor="#ffffff", edgecolor="#cbd5e1")

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# -----------------------------------------------------------------------------
# Main PDF Report Generator
# -----------------------------------------------------------------------------
class SolarDailyReportGenerator:
    """
    Builds clean, client-ready solar telemetry daily reports in PDF format.
    Accepts Phase3Result and produces an A4 multi-section document.
    """

    def __init__(
        self,
        output_dir: Optional[Path | str] = None,
        site_name: Optional[str] = None,
    ) -> None:
        self.output_dir = Path(output_dir) if output_dir else Path("reports")
        self.site_name = site_name

    def generate(
        self,
        result: Phase3Result,
        site_config: Optional[SiteConfig] = None,
        custom_filename: Optional[str] = None,
    ) -> Path:
        """
        Renders the complete solar daily report PDF.
        Returns the absolute Path of the created file.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)

        gen_time_utc = datetime.datetime.now(datetime.timezone.utc)
        gen_time_ist = to_ist(gen_time_utc)
        date_str = gen_time_ist.strftime("%Y%m%d_%H%M%S")

        if custom_filename:
            filename = custom_filename if custom_filename.endswith(".pdf") else f"{custom_filename}.pdf"
        else:
            serial_clean = "".join(c for c in result.logger_serial if c.isalnum() or c in "-_")
            filename = f"solar_report_{serial_clean}_{date_str}.pdf"

        target_file = (self.output_dir / filename).resolve()

        doc = SimpleDocTemplate(
            str(target_file),
            pagesize=A4,
            leftMargin=36,
            rightMargin=36,
            topMargin=36,
            bottomMargin=42,
        )

        styles = self._build_styles()
        story: list[Any] = []

        # 1. Header & Metadata
        self._add_header_section(story, result, site_config, gen_time_ist, styles)

        # 2. Executive Summary
        self._add_executive_summary(story, result, site_config, styles)

        # 3. Solar Metrics (F01 - F30 breakdown)
        self._add_solar_metrics_section(story, result, styles)

        # 4. Telemetry Time-Series Chart
        self._add_chart_section(story, result, styles)

        # 5. Data Quality & Collection Status
        self._add_data_quality_section(story, result, styles)

        # 6. Limitations & Disclaimers
        self._add_limitations_section(story, result, styles)

        # Build PDF using NumberedCanvas
        doc.build(story, canvasmaker=NumberedCanvas)
        return target_file

    def _build_styles(self) -> dict[str, ParagraphStyle]:
        """Configures typography styles for clean readability and tight alignment."""
        base_sheets = getSampleStyleSheet()
        styles: dict[str, ParagraphStyle] = {}

        styles["Title"] = ParagraphStyle(
            "DocTitle",
            parent=base_sheets["Normal"],
            fontName="Helvetica-Bold",
            fontSize=18,
            leading=22,
            textColor=colors.HexColor("#0f172a"),
        )
        styles["Subtitle"] = ParagraphStyle(
            "DocSubTitle",
            parent=base_sheets["Normal"],
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=colors.HexColor("#475569"),
        )
        styles["Badge"] = ParagraphStyle(
            "BadgeStyle",
            parent=base_sheets["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#b45309"),
            alignment=1,  # Centered
        )
        styles["SectionHeading"] = ParagraphStyle(
            "SectionH",
            parent=base_sheets["Normal"],
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=16,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=10,
            spaceAfter=4,
        )
        styles["SubSectionHeading"] = ParagraphStyle(
            "SubSectionH",
            parent=base_sheets["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#334155"),
            spaceBefore=6,
            spaceAfter=2,
        )
        styles["Body"] = ParagraphStyle(
            "DocBody",
            parent=base_sheets["Normal"],
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=colors.HexColor("#1e293b"),
        )
        styles["BodyBold"] = ParagraphStyle(
            "DocBodyBold",
            parent=styles["Body"],
            fontName="Helvetica-Bold",
        )
        styles["BodyMuted"] = ParagraphStyle(
            "DocBodyMuted",
            parent=styles["Body"],
            fontSize=8,
            leading=10.5,
            textColor=colors.HexColor("#64748b"),
        )
        styles["CellText"] = ParagraphStyle(
            "TableCell",
            parent=base_sheets["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#1e293b"),
        )
        styles["CellTextBold"] = ParagraphStyle(
            "TableCellBold",
            parent=styles["CellText"],
            fontName="Helvetica-Bold",
        )
        styles["CellTextMuted"] = ParagraphStyle(
            "TableCellMuted",
            parent=styles["CellText"],
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#64748b"),
        )
        styles["WarningText"] = ParagraphStyle(
            "WarnText",
            parent=styles["CellText"],
            textColor=colors.HexColor("#b91c1c"),
            fontName="Helvetica-Bold",
        )
        styles["SuccessText"] = ParagraphStyle(
            "SuccText",
            parent=styles["CellText"],
            textColor=colors.HexColor("#15803d"),
            fontName="Helvetica-Bold",
        )
        return styles

    # -------------------------------------------------------------------------
    # Section Builders
    # -------------------------------------------------------------------------
    def _add_header_section(
        self,
        story: list[Any],
        result: Phase3Result,
        site_config: Optional[SiteConfig],
        gen_time_ist: datetime.datetime,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        title_text = "Solar Telemetry & Daily Report"
        sub_text = self.site_name or (site_config.timezone_name if site_config else "Solar PV Telemetry Monitoring")

        story.append(Paragraph(title_text, styles["Title"]))
        story.append(Paragraph(f"Installation: {sub_text}", styles["Subtitle"]))
        story.append(Spacer(1, 6))

        # Partial-window disclaimer banner
        badge_p = Paragraph(
            "REPORTING WINDOW: PARTIAL TELEMETRY CAPTURE &bull; NOT A FULL 24-HOUR DAILY SUMMARY",
            styles["Badge"],
        )
        badge_table = Table([[badge_p]], colWidths=[A4[0] - 72])
        badge_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fef3c7")),
                ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#f59e0b")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ])
        )
        story.append(badge_table)
        story.append(Spacer(1, 8))

        # Metadata grid
        duration_desc = f"{result.last_receipt_time - result.first_receipt_time}" if (result.first_receipt_time and result.last_receipt_time) else "N/A"
        if result.first_receipt_time and result.last_receipt_time:
            dur_secs = (result.last_receipt_time - result.first_receipt_time).total_seconds()
            duration_desc = f"{int(dur_secs)} seconds ({int(dur_secs // 60)}m {int(dur_secs % 60)}s)"

        meta_data = [
            [
                Paragraph("<b>Logger Serial:</b>", styles["CellText"]),
                Paragraph(result.logger_serial, styles["CellTextBold"]),
                Paragraph("<b>Broker Host:</b>", styles["CellText"]),
                Paragraph(f"{result.broker}:{result.port}", styles["CellText"]),
            ],
            [
                Paragraph("<b>Target Topic:</b>", styles["CellText"]),
                Paragraph(result.topic, styles["CellText"]),
                Paragraph("<b>Collection Status:</b>", styles["CellText"]),
                Paragraph(
                    f"<b>{result.collection_status}</b>",
                    styles["SuccessText"] if result.collection_status == "SUCCESS" else styles["WarningText"],
                ),
            ],
            [
                Paragraph("<b>Collection Start:</b>", styles["CellText"]),
                Paragraph(format_ist_str(result.first_receipt_time), styles["CellText"]),
                Paragraph("<b>Collection End:</b>", styles["CellText"]),
                Paragraph(format_ist_str(result.last_receipt_time), styles["CellText"]),
            ],
            [
                Paragraph("<b>Captured Duration:</b>", styles["CellText"]),
                Paragraph(duration_desc, styles["CellText"]),
                Paragraph("<b>Report Generated:</b>", styles["CellText"]),
                Paragraph(format_ist_str(gen_time_ist), styles["CellTextBold"]),
            ],
        ]

        meta_table = Table(meta_data, colWidths=[90, 170, 90, 173])
        meta_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(meta_table)
        story.append(Spacer(1, 10))

    def _add_executive_summary(
        self,
        story: list[Any],
        result: Phase3Result,
        site_config: Optional[SiteConfig],
        styles: dict[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("1. Executive Summary", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284c7"), spaceAfter=6))

        # Retrieve key formulas
        f24 = result.formula_results.get("F24")
        online_status = f24.value if f24 and f24.value else "UNKNOWN"
        f01 = result.formula_results.get("F01")
        f02 = result.formula_results.get("F02")
        f25 = result.formula_results.get("F25")
        f28 = result.formula_results.get("F28")

        summary_p1 = (
            f"During the captured collection window, <b>{result.valid_samples}</b> valid telemetry packet(s) were received "
            f"out of <b>{result.total_messages}</b> total messages. System connectivity state is evaluated as "
            f"<b>{online_status}</b> based on telemetry freshness."
        )
        story.append(Paragraph(summary_p1, styles["Body"]))
        story.append(Spacer(1, 4))

        # Metrics status box
        f01_val = f"{f01.value} {f01.unit}" if (f01 and f01.status == "CALCULATED" and f01.value is not None) else "N/A (Units Unverified)"
        f02_val = f"{f02.value} {f02.unit} (Partial)" if (f02 and f02.status == "PARTIAL" and f02.value is not None) else "N/A"
        temp_val = f"{f25.value} (raw)" if (f25 and f25.status == "CALCULATED" and f25.value is not None) else "N/A"

        alarm_label = "None detected (software state only)"
        if f28 and isinstance(f28.value, dict):
            alarm_label = f"Code {f28.value.get('inverter_status_code')} (Software Status Only)"

        summary_cards = [
            [
                Paragraph("<b>Interval Energy (F01)</b>", styles["CellTextMuted"]),
                Paragraph("<b>Period Energy (F02)</b>", styles["CellTextMuted"]),
                Paragraph("<b>Inverter Temp (F25)</b>", styles["CellTextMuted"]),
                Paragraph("<b>Inverter State (F28)</b>", styles["CellTextMuted"]),
            ],
            [
                Paragraph(f"<b>{f01_val}</b>", styles["CellTextBold"]),
                Paragraph(f"<b>{f02_val}</b>", styles["CellTextBold"]),
                Paragraph(f"<b>{temp_val}</b>", styles["CellTextBold"]),
                Paragraph(f"<b>{alarm_label}</b>", styles["CellText"]),
            ],
        ]

        summary_table = Table(summary_cards, colWidths=[130, 130, 110, 153])
        summary_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ])
        )
        story.append(summary_table)
        story.append(Spacer(1, 10))

    def _add_solar_metrics_section(
        self,
        story: list[Any],
        result: Phase3Result,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("2. Solar Formula Engine Results (F01 &ndash; F30)", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284c7"), spaceAfter=6))

        story.append(
            Paragraph(
                "Metrics evaluated in accordance with the <i>Solar Data Logger Formula Profile</i>. "
                "Metrics lacking verified field scalings or required physical sensor feeds (e.g., plane-of-array irradiance, bidirectional meters) "
                "are explicitly marked <b>N/A</b> with documented missing prerequisites.",
                styles["BodyMuted"],
            )
        )
        story.append(Spacer(1, 6))

        headers = [
            Paragraph("<b>ID</b>", styles["CellTextBold"]),
            Paragraph("<b>Metric Name</b>", styles["CellTextBold"]),
            Paragraph("<b>Status</b>", styles["CellTextBold"]),
            Paragraph("<b>Value</b>", styles["CellTextBold"]),
            Paragraph("<b>Unit</b>", styles["CellTextBold"]),
            Paragraph("<b>Prerequisites &amp; Interpretation</b>", styles["CellTextBold"]),
        ]
        table_rows = [headers]

        # Key core formulas to display in table
        priority_fids = [
            "F01", "F02", "F03", "F04", "F05", "F06", "F07",
            "F11", "F12", "F13", "F14", "F16", "F17", "F21", "F22",
            "F24", "F25", "F28", "F29", "F30"
        ]

        for fid in priority_fids:
            f = result.formula_results.get(fid)
            if not f:
                continue

            status_style = styles["CellText"]
            if f.status == "CALCULATED":
                status_style = styles["SuccessText"]
            elif f.status == "PARTIAL":
                status_style = styles["CellTextBold"]
            elif f.status == "N/A":
                status_style = styles["CellTextMuted"]

            val_str = "N/A"
            if f.value is not None:
                if isinstance(f.value, float):
                    val_str = f"{f.value:,.2f}"
                elif isinstance(f.value, dict):
                    val_str = str(f.value.get("coverage_pct") or f.value.get("inverter_status_code") or "Structured")
                else:
                    val_str = str(f.value)

            unit_str = f.unit or "&mdash;"

            # Prerequisite or explanation note
            if f.status == "N/A":
                note = f"Missing: {', '.join(f.missing_inputs[:2])}" if f.missing_inputs else f.explanation
            else:
                note = f.explanation[:90] + ("..." if len(f.explanation) > 90 else "")

            table_rows.append([
                Paragraph(f"<b>{f.formula_id}</b>", styles["CellTextBold"]),
                Paragraph(f.name, styles["CellText"]),
                Paragraph(f.status, status_style),
                Paragraph(val_str, styles["CellTextBold"]),
                Paragraph(unit_str, styles["CellText"]),
                Paragraph(note, styles["CellTextMuted"]),
            ])

        metrics_table = Table(
            table_rows,
            colWidths=[30, 135, 60, 65, 55, 178],
            repeatRows=1,
        )
        metrics_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ])
        )

        story.append(metrics_table)
        story.append(Spacer(1, 10))

    def _add_chart_section(
        self,
        story: list[Any],
        result: Phase3Result,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("3. Telemetry Time-Series Chart", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284c7"), spaceAfter=6))

        chart_buf = generate_active_power_chart(result.samples)
        if chart_buf:
            img = Image(chart_buf, width=A4[0] - 72, height=130)
            story.append(img)
            story.append(Spacer(1, 4))
            story.append(
                Paragraph(
                    "<i>Note: Values represent raw readings from telemetry. Field scaling requires manufacturer unit confirmation before engineering unit conversion.</i>",
                    styles["CellTextMuted"],
                )
            )
        else:
            insufficient_msg = Paragraph(
                "<b>Insufficient Data for Time-Series Visualization:</b> "
                "The current collection window contains fewer than 2 valid timestamped readings. "
                "No synthetic points have been fabricated.",
                styles["CellTextMuted"],
            )
            box = Table([[insufficient_msg]], colWidths=[A4[0] - 72])
            box.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ])
            )
            story.append(box)

        story.append(Spacer(1, 10))

    def _add_data_quality_section(
        self,
        story: list[Any],
        result: Phase3Result,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("4. Data Quality & Collection Health", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284c7"), spaceAfter=6))

        dq_data = [
            [
                Paragraph("<b>Total Messages Received:</b>", styles["CellText"]),
                Paragraph(str(result.total_messages), styles["CellTextBold"]),
                Paragraph("<b>Valid Samples Parsed:</b>", styles["CellText"]),
                Paragraph(str(result.valid_samples), styles["CellTextBold"]),
            ],
            [
                Paragraph("<b>Invalid / Malformed JSON:</b>", styles["CellText"]),
                Paragraph(str(result.invalid_samples), styles["CellTextBold"]),
                Paragraph("<b>Duplicate Packets:</b>", styles["CellText"]),
                Paragraph(str(result.duplicate_samples), styles["CellTextBold"]),
            ],
            [
                Paragraph("<b>Out-of-Order Readings:</b>", styles["CellText"]),
                Paragraph(str(result.out_of_order_samples), styles["CellTextBold"]),
                Paragraph("<b>Counter Drop / Resets:</b>", styles["CellText"]),
                Paragraph(str(result.counter_resets), styles["CellTextBold"]),
            ],
        ]

        dq_table = Table(dq_data, colWidths=[130, 130, 130, 133])
        dq_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ])
        )
        story.append(dq_table)
        story.append(Spacer(1, 6))

        # Warnings list if any
        if result.all_warnings:
            story.append(Paragraph("<b>Validation &amp; Integrity Warnings:</b>", styles["SubSectionHeading"]))
            for w in result.all_warnings[:4]:
                story.append(Paragraph(f"&bull; {w}", styles["CellTextMuted"]))
            if len(result.all_warnings) > 4:
                story.append(Paragraph(f"<i>... and {len(result.all_warnings) - 4} additional warnings.</i>", styles["CellTextMuted"]))
            story.append(Spacer(1, 6))

    def _add_limitations_section(
        self,
        story: list[Any],
        result: Phase3Result,
        styles: dict[str, ParagraphStyle],
    ) -> None:
        story.append(Paragraph("5. Technical Limitations & Engineering Disclaimers", styles["SectionHeading"]))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#0284c7"), spaceAfter=6))

        disclaimers = [
            "<b>Partial Collection Window:</b> This report reflects only telemetry received during the active MQTT collection window. It does not represent a full 24-hour daily generation total.",
            "<b>IEC Performance Ratio (F05) Distinction:</b> Simple generation benchmark achievement (F04) is based on theoretical Peak Sun Hours and is not equivalent to an IEC 61724 Performance Ratio, which requires calibrated on-site plane-of-array pyranometer data.",
            "<b>Gross Generation Value vs Actual Savings:</b> Gross generation estimates (F17) represent the gross value of produced power and do not constitute actual utility bill savings without bidirectional meter data and net-metering settlement rules.",
            "<b>Hardware & Wiring Safety:</b> Software state reporting (F28) reflects only gateway status codes and does not verify electrical wiring safety, phase balance integrity, or fire compliance.",
            "<b>Engineering Unit Verification:</b> Telemetry scalings (e.g. active power, energy counters) require formal vendor documentation before being converted to standard SI units without error bounds.",
        ]

        for d in disclaimers:
            story.append(Paragraph(f"&bull; {d}", styles["Body"]))
            story.append(Spacer(1, 3))
