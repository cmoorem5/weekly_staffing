"""
Annual (fiscal-year) staffing report for executive review (data from staffing.db).

Same KPIs, exception breakdown, role volumes, and base coverage as the
quarterly report, loaded through ``load_window_report_data`` over the full FY.
The detail and trend run month to month instead of week by week: a week
belongs to the calendar month its Sunday ``week_start`` falls in (the same
rule the monthly board pack uses), so FY edge months can hold only a few weeks
and say so in the Weeks column. Monthly figures are the mean of that month's
weekly ratios, matching the KPI averages.

Visual style: staffing_tool/report_style.py + docs/BMF_Visual_Style_Spec.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from staffing_tool import report_style as style
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import (
    fy_end_date,
    fy_label_year,
    fy_week1_for_label_year,
    fy_week1_sunday_containing,
)
from staffing_tool.leave_grid import EXCEPTION_GRID_COLS
from staffing_tool.metrics import WeekMetrics, compute_period_rollups
from staffing_tool.models import WeeklyStaffing
from staffing_tool.quarterly_pdf_report import (
    QuarterlyReportContext,
    _kpis_with_deltas,
    _period_volumes_table,
    load_window_report_data,
)

MONTHLY_HEADERS = [
    "Month",
    "Weeks",
    "Staffing Rate",
    "OT Dependency",
    "Shift Exception %",
    "System RW %",
    "System GR %",
]
VOLUME_HEADERS = [
    "Role",
    "RN Shifts",
    "PM Shifts",
    "Total Shifts",
    "Exceptions",
    "OT RN",
    "OT PM",
]


@dataclass
class AnnualReportContext:
    fy_label_year: int
    period: str
    dates: str
    weeks_expected: int
    window: QuarterlyReportContext
    # (label, staffing %, OT %, exception %) per month, 0-100 scale
    monthly_trend: list[tuple[str, float, float, float]] = field(default_factory=list)
    monthly_detail: list[tuple[str, ...]] = field(default_factory=list)

    @property
    def weeks_count(self) -> int:
        return self.window.weeks_count

    @property
    def completeness_note(self) -> str:
        have, want = self.weeks_count, self.weeks_expected
        if have >= want:
            return f"All {want} weeks of {self.period} are in the database."
        return (
            f"{have} of {want} weeks of {self.period} are in the database; "
            f"averages cover the {have} imported weeks only."
        )


def _fy_window(fy: int) -> tuple[date, date]:
    w1 = fy_week1_for_label_year(fy)
    if w1 is None:
        raise ValueError(f"No fiscal year found for FY{fy}.")
    return w1, fy_end_date(w1)


def list_fiscal_years(db_path: str) -> list[dict[str, Any]]:
    """Fiscal years with at least one week of staffing data, newest first."""
    with session_scope(db_path) as session:
        week_starts = [r[0] for r in session.query(WeeklyStaffing.week_start).all()]
    labels = {
        fy_label_year(
            fy_week1_sunday_containing(datetime.strptime(ws, "%Y-%m-%d").date())
        )
        for ws in week_starts
    }
    out = []
    for lab in sorted(labels, reverse=True):
        start, end = _fy_window(lab)
        out.append(
            {
                "fy_label_year": lab,
                "period": f"FY{lab}",
                "date_start": start.isoformat(),
                "date_end": end.isoformat(),
            }
        )
    return out


def _monthly_rows(
    metrics: list[WeekMetrics],
) -> tuple[list[tuple[str, float, float, float]], list[tuple[str, ...]]]:
    by_month: dict[tuple[int, int], list[WeekMetrics]] = {}
    for wm in metrics:
        d = datetime.strptime(wm.week_start, "%Y-%m-%d").date()
        by_month.setdefault((d.year, d.month), []).append(wm)

    trend: list[tuple[str, float, float, float]] = []
    detail: list[tuple[str, ...]] = []
    for (year, month), weeks in sorted(by_month.items()):
        r = compute_period_rollups(weeks)
        if r is None:
            continue
        label = date(year, month, 1).strftime("%b %Y")
        trend.append(
            (
                date(year, month, 1).strftime("%b %y"),
                r.avg_staffing_rate * 100,
                r.avg_ot_dependency * 100,
                r.avg_leave_exposure * 100,
            )
        )
        detail.append(
            (
                label,
                str(r.n_weeks),
                style.pct(r.avg_staffing_rate),
                style.pct(r.avg_ot_dependency),
                style.pct(r.avg_leave_exposure),
                style.pct(r.avg_system_rw_pct),
                style.pct(r.avg_system_gr_pct),
            )
        )
    return trend, detail


def load_annual_report_data(db_path: str, fy: int) -> AnnualReportContext:
    start, end = _fy_window(fy)
    period = f"FY{fy}"
    window = load_window_report_data(
        db_path, start, end, period=period, fy_label_year=fy, quarter=0
    )
    trend, detail = _monthly_rows(window.week_metrics)
    return AnnualReportContext(
        fy_label_year=fy,
        period=period,
        dates=window.dates,
        weeks_expected=((end - start).days + 1) // 7,
        window=window,
        monthly_trend=trend,
        monthly_detail=detail,
    )


def _build_trend_fig(ctx: AnnualReportContext):
    return style.trend_fig(
        ctx.monthly_trend,
        height_in=3.6,
        exception_label="Shift Exception %",
        xtick_fontsize=7,
        xtick_rotation=30,
        xtick_ha="right",
        targets=ctx.window.trend_targets,
    )


def _monthly_detail_table(ctx: AnnualReportContext) -> Table:
    col_w = style.full_width_col_widths([1.3, 0.7, 1.1, 1.1, 1.3, 1.1, 1.1])
    rows = [MONTHLY_HEADERS] + [list(r) for r in ctx.monthly_detail]
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            style.data_table_style()
            + [
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [style.WHITE, style.LGRAY]),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
            + style.num_style_cells([1, 2, 3, 4, 5, 6])
        )
    )
    return t


def build_pdf(ctx: AnnualReportContext, output_path: str) -> str:
    style.register_fonts()
    w = ctx.window
    on_first, on_later = style.make_page_callbacks(
        footer_short_title=f"Annual Staffing {ctx.period}",
        running_header_title=f"Annual Staffing Report — {ctx.period}",
    )
    doc = SimpleDocTemplate(
        output_path,
        pagesize=style.PAGE_SIZE,
        leftMargin=style.MARGIN,
        rightMargin=style.MARGIN,
        topMargin=style.MARGIN,
        bottomMargin=style.MARGIN,
    )
    note_style = ParagraphStyle(
        "completeness",
        fontName=style.F("BarlowRegular"),
        fontSize=8,
        textColor=style.NAVY,
    )
    story = [
        style.title_banner(
            f"ANNUAL STAFFING REPORT — {ctx.period}",
            f"{ctx.dates}  |  {ctx.weeks_count}-Week Period",
            meta_line=f"Prepared {w.prepared_date} · CONFIDENTIAL",
        ),
        Spacer(1, 10),
        style.section_bar("KEY PERFORMANCE INDICATORS — FISCAL YEAR AVERAGES"),
        style.kpi_row(w.kpi_data),
        Spacer(1, 4),
        Paragraph(ctx.completeness_note, note_style),
        Spacer(1, 10),
        style.section_bar("MONTHLY TREND"),
        style.chart_to_image(_build_trend_fig(ctx), style.USABLE_W),
        Spacer(1, 10),
        style.section_bar("MONTH-BY-MONTH DETAIL"),
        _monthly_detail_table(ctx),
        Spacer(1, 10),
        style.section_bar("EXCEPTION BREAKDOWN"),
        style.chart_to_image(
            style.exception_bar_fig(w.leave_breakdown), style.USABLE_W, 1.8 * inch
        ),
        Spacer(1, 10),
        style.section_bar("SCHEDULE EXCEPTIONS"),
        style.exception_table(w.leave_breakdown),
        Spacer(1, 10),
        style.section_bar("ANNUAL VOLUMES"),
        _period_volumes_table(w),
        Spacer(1, 10),
        style.section_bar("COVERAGE BY BASE"),
        style.base_coverage_table(w.base_coverage, [1.8, 1.3, 1.3, 1.3, 1.8]),
        Spacer(1, 10),
    ]
    doc.build(
        story,
        onFirstPage=on_first,
        onLaterPages=on_later,
        canvasmaker=style.NumberedCanvas,
    )
    return output_path


def build_html(
    ctx: AnnualReportContext,
    output_path: str,
    prior_ctx: AnnualReportContext | None = None,
) -> str:
    from staffing_tool import report_html as rh

    w = ctx.window
    kpi_note = rh.note(ctx.completeness_note)
    if prior_ctx:
        kpi_note += rh.note(
            f"Change shown vs {prior_ctx.period} (percentage points; "
            f"{prior_ctx.weeks_count} weeks of data)."
        )
    body = rh.section_bar("KEY PERFORMANCE INDICATORS — FISCAL YEAR AVERAGES")
    body += rh.body_cell(
        rh.kpi_strip(_kpis_with_deltas(w, prior_ctx.window if prior_ctx else None))
        + kpi_note
    )

    if ctx.monthly_trend:
        body += rh.section_bar("MONTHLY TREND")
        body += rh.body_cell(
            rh.chart_img(rh.fig_to_png_base64(_build_trend_fig(ctx)), "Monthly trend")
        )

    body += rh.section_bar("MONTH-BY-MONTH DETAIL")
    body += rh.body_cell(
        rh.data_table(
            MONTHLY_HEADERS,
            [list(r) for r in ctx.monthly_detail],
            right_cols={1, 2, 3, 4, 5, 6},
        )
    )

    top2 = style.leave_top2(w.leave_breakdown)
    top2_note = ", ".join(
        f"{code} ({count})"
        for code, count in sorted(w.leave_breakdown, key=lambda r: r[1], reverse=True)
        if code in top2
    )
    grid_label = " &middot; ".join(EXCEPTION_GRID_COLS)
    body += rh.section_bar(f"EXCEPTION BREAKDOWN ({grid_label})")
    body += rh.body_cell(
        rh.chart_img(
            rh.fig_to_png_base64(style.exception_bar_fig(w.leave_breakdown)),
            "Exception breakdown",
        )
        + '<div style="height:12px;"></div>'
        + rh.exception_mix_table(w.leave_breakdown, top2)
        + rh.note(f"Top drivers (red in chart): {top2_note or 'n/a'}.")
    )

    body += rh.section_bar("ANNUAL VOLUMES BY ROLE")
    body += rh.body_cell(
        rh.data_table(
            VOLUME_HEADERS,
            [list(r) for r in w.period_volumes] + [list(w.period_vol_total)],
            right_cols={1, 2, 3, 4, 5, 6},
            total_row=True,
        )
    )

    body += rh.section_bar("COVERAGE BY BASE")
    body += rh.body_cell(
        rh.data_table(
            ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"],
            [list(r) for r in w.base_coverage],
            right_cols={1, 2, 3, 4},
        )
    )

    html = rh.report_shell(
        title="ANNUAL STAFFING REPORT",
        subtitle=f"{ctx.period} &nbsp;|&nbsp; {ctx.dates}",
        meta=(
            f"Weeks included: {ctx.weeks_count} of {ctx.weeks_expected} &middot; "
            f"Prepared {w.prepared_date} &middot; CONFIDENTIAL"
        ),
        body=body,
        doc_title=f"Annual Staffing Report — {ctx.period}",
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def _output_path(output_dir: str, ctx: AnnualReportContext, ext: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    return os.path.join(output_dir, f"BMF_Annual_Staffing_{ctx.period}.{ext}")


def export_annual_staffing_pdf(db_path: str, fy: int, output_dir: str) -> str:
    ctx = load_annual_report_data(db_path, fy)
    return build_pdf(ctx, _output_path(output_dir, ctx, "pdf"))


def export_annual_staffing_html(db_path: str, fy: int, output_dir: str) -> str:
    ctx = load_annual_report_data(db_path, fy)
    prior_ctx = None
    try:
        prior_ctx = load_annual_report_data(db_path, fy - 1)
    except ValueError:
        pass  # first FY with data — no comparison to show
    return build_html(ctx, _output_path(output_dir, ctx, "html"), prior_ctx)
