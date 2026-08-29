"""
Quarterly staffing PDF report builder (data from staffing.db).

Visual style: staffing_tool/report_style.py + docs/BMF_Visual_Style_Spec.md
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, cast

from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from staffing_tool import report_style as style
from staffing_tool.db import session_scope
from staffing_tool.fiscal_year import (
    fiscal_quarter_windows_for_fy,
    fy_label_year,
    fy_week1_for_label_year,
    fy_week1_sunday_containing,
)
from staffing_tool.leave_grid import EXCEPTION_GRID_COLS
from staffing_tool.metrics import (
    BASE_DISPLAY_ORDER,
    ROLE_CAPACITY_PER_WEEK,
    TOTAL_PERSON_SHIFTS,
    compute_period_rollups,
    compute_week_metrics,
)
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyStaffing,
)

EM = "\u2014"
BASE_ORDER = BASE_DISPLAY_ORDER
RN_PER_WEEK = ROLE_CAPACITY_PER_WEEK["RN"]
MEDIC_PER_WEEK = ROLE_CAPACITY_PER_WEEK["MEDIC"]
EMT_PER_WEEK = ROLE_CAPACITY_PER_WEEK["EMT"]


@dataclass
class QuarterlyReportContext:
    fy_label_year: int
    quarter: int
    period: str
    dates: str
    weeks_count: int
    prepared_date: str
    kpi_data: list[tuple[str, str]]
    weekly_trend: list[tuple[str, float, float, float]]
    leave_breakdown: list[tuple[str, int]]
    period_volumes: list[tuple[str, str, str, str, str, str, str]]
    period_vol_total: tuple[str, str, str, str, str, str, str]
    base_coverage: list[tuple[str, str, str, str, str]]
    weekly_detail: list[tuple[str, str, str, str]]


def _pct(v: float) -> str:
    return style.pct(v)


def _short_label(iso: str) -> str:
    return style.short_label(iso)


def _format_date_range(start: date, end: date) -> str:
    if start.year == end.year:
        if start.month == end.month:
            return f"{start.strftime('%B %d')} \u2013 {end.day}, {end.year}"
        return f"{start.strftime('%B %d')} \u2013 {end.strftime('%B %d, %Y')}"
    return f"{start.strftime('%B %d, %Y')} \u2013 {end.strftime('%B %d, %Y')}"


def _week_overlaps_quarter(week_start: str, q_start: date, q_end: date) -> bool:
    d = datetime.strptime(week_start, "%Y-%m-%d").date()
    return d <= q_end and d + timedelta(days=6) >= q_start


def _quarter_window(fy_label_year: int, quarter: int) -> tuple[date, date]:
    fy_w1 = fy_week1_for_label_year(fy_label_year)
    if fy_w1 is None:
        raise ValueError(f"No fiscal year found for FY{fy_label_year}.")
    for q, qa, qb in fiscal_quarter_windows_for_fy(fy_w1):
        if q == quarter:
            return qa, qb
    raise ValueError(f"Quarter {quarter} is not valid for FY{fy_label_year}.")


def list_fiscal_quarters(db_path: str) -> list[dict[str, Any]]:
    """Quarters that have at least one week of staffing data."""
    seen: dict[tuple[int, int], dict[str, Any]] = {}
    with session_scope(db_path) as session:
        week_starts = [
            r[0]
            for r in session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start)
            .all()
        ]
    for ws in week_starts:
        d = datetime.strptime(ws, "%Y-%m-%d").date()
        fy_w1 = fy_week1_sunday_containing(d)
        lab = fy_label_year(fy_w1)
        for q, qa, qb in fiscal_quarter_windows_for_fy(fy_w1):
            if _week_overlaps_quarter(ws, qa, qb):
                key = (lab, q)
                if key not in seen:
                    seen[key] = {
                        "fy_label_year": lab,
                        "quarter": q,
                        "period": f"FY{lab} Q{q}",
                        "date_start": qa.isoformat(),
                        "date_end": qb.isoformat(),
                    }
    return sorted(
        seen.values(),
        key=lambda x: (x["fy_label_year"], x["quarter"]),
        reverse=True,
    )


def load_quarter_report_data(
    db_path: str,
    fy_label_year: int,
    quarter: int,
) -> QuarterlyReportContext:
    q_start, q_end = _quarter_window(fy_label_year, quarter)
    period = f"FY{fy_label_year} Q{quarter}"
    dates = _format_date_range(q_start, q_end)

    with session_scope(db_path) as session:
        all_weeks = [
            r[0]
            for r in session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start)
            .all()
        ]
        week_starts = sorted(
            ws for ws in all_weeks if _week_overlaps_quarter(ws, q_start, q_end)
        )
        if not week_starts:
            raise ValueError(
                f"No staffing data for {period} ({q_start.isoformat()} to {q_end.isoformat()})."
            )

        base_configs = session.query(BaseConfig).all()
        cfg_by_name = {b.base_name: b for b in base_configs}
        metrics_list = []
        weekly_trend: list[tuple[str, float, float, float]] = []
        weekly_detail: list[tuple[str, str, str, str]] = []

        leave_totals = {code: 0 for code in EXCEPTION_GRID_COLS}
        exc_by_role = {"RN": 0, "Medic": 0, "EMT": 0}
        ot_rn = ot_medic = ot_emt = 0
        base_rw: dict[str, int] = {b: 0 for b in BASE_ORDER}
        base_gr: dict[str, int] = {b: 0 for b in BASE_ORDER}

        for ws in week_starts:
            row = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == ws)
                .first()
            )
            if not row:
                continue
            coverages = (
                session.query(WeeklyBaseCoverage)
                .filter(WeeklyBaseCoverage.week_start == ws)
                .all()
            )
            wm = compute_week_metrics(row, coverages, base_configs)
            metrics_list.append(wm)
            weekly_trend.append(
                (
                    _short_label(ws),
                    wm.staffing_rate * 100,
                    wm.ot_dependency * 100,
                    wm.leave_exposure * 100,
                )
            )
            weekly_detail.append(
                (
                    _short_label(ws),
                    _pct(wm.staffing_rate),
                    _pct(wm.ot_dependency),
                    _pct(wm.leave_exposure),
                )
            )

            ws_row = cast(Any, row)
            leave_totals["AT"] += int(ws_row.leave_at or 0)
            leave_totals["LT"] += int(ws_row.leave_lt or 0)
            leave_totals["SICK"] += int(ws_row.leave_sick or 0)
            leave_totals["LOA"] += int(ws_row.leave_loa or 0) + int(
                ws_row.leave_pfml or 0
            )
            leave_totals["JURY"] += int(ws_row.leave_jury or 0)
            leave_totals["BREV"] += int(ws_row.leave_brev or 0)

            ot_rn += int(ws_row.ot_rn or 0)
            ot_medic += int(ws_row.ot_medic or 0)
            ot_emt += int(ws_row.ot_emt or 0)

            leave_details = (
                session.query(WeeklyLeaveDetail)
                .filter(WeeklyLeaveDetail.week_start == ws)
                .all()
            )
            for ld in leave_details:
                if ld.role in exc_by_role:
                    exc_by_role[ld.role] += int(ld.count)

            bm = wm.base_metrics or {}
            for base in BASE_ORDER:
                m = bm.get(base, {})
                base_rw[base] += int(m.get("rw_staffed", 0))
                base_gr[base] += int(m.get("gr_staffed", 0))

        rollups = compute_period_rollups(metrics_list)
        if rollups is None:
            raise ValueError(f"Could not compute rollups for {period}.")

        n = rollups.n_weeks
        leave_breakdown = [(code, leave_totals[code]) for code in EXCEPTION_GRID_COLS]

        rn_cap = str(RN_PER_WEEK * n)
        medic_cap = str(MEDIC_PER_WEEK * n)
        emt_cap = str(EMT_PER_WEEK * n)
        total_cap = str(TOTAL_PERSON_SHIFTS * n)
        period_volumes = [
            (
                "RN",
                rn_cap,
                EM,
                rn_cap,
                str(exc_by_role["RN"]),
                str(ot_rn),
                EM,
            ),
            (
                "Paramedic (Flight Medic)",
                EM,
                medic_cap,
                medic_cap,
                str(exc_by_role["Medic"]),
                EM,
                str(ot_medic),
            ),
            (
                "EMT (Ground)",
                EM,
                EM,
                emt_cap,
                str(exc_by_role["EMT"]),
                EM,
                EM,
            ),
        ]
        period_vol_total = (
            "Total",
            EM,
            EM,
            total_cap,
            str(rollups.leave_total),
            str(ot_rn),
            str(ot_medic),
        )

        base_coverage: list[tuple[str, str, str, str, str]] = []
        for base in BASE_ORDER:
            cfg = cfg_by_name.get(base)
            rw_cap = int(cfg.rw_total_unit_days) * n if cfg else 0
            gr_cap = int(cfg.gr_total_unit_days) * n if cfg else 0
            rw_n = base_rw.get(base, 0)
            gr_n = base_gr.get(base, 0)
            base_coverage.append(
                (
                    base,
                    str(rw_n) if rw_n else EM,
                    _pct(rw_n / rw_cap)
                    if rw_cap and rw_n
                    else (EM if not rw_cap else "0.0%"),
                    str(gr_n) if gr_n else EM,
                    _pct(gr_n / gr_cap)
                    if gr_cap and gr_n
                    else (EM if not gr_cap else "0.0%"),
                )
            )

        return QuarterlyReportContext(
            fy_label_year=fy_label_year,
            quarter=quarter,
            period=period,
            dates=dates,
            weeks_count=n,
            prepared_date=date.today().strftime("%B %d, %Y"),
            kpi_data=[
                ("Avg Staffing Rate", _pct(rollups.avg_staffing_rate)),
                ("Avg OT Dependency", _pct(rollups.avg_ot_dependency)),
                ("Avg Shift Exception %", _pct(rollups.avg_leave_exposure)),
                ("Avg System RW %", _pct(rollups.avg_system_rw_pct)),
                ("Avg System GR %", _pct(rollups.avg_system_gr_pct)),
                ("Person-Shifts / Week", str(TOTAL_PERSON_SHIFTS)),
            ],
            weekly_trend=weekly_trend,
            leave_breakdown=leave_breakdown,
            period_volumes=period_volumes,
            period_vol_total=period_vol_total,
            base_coverage=base_coverage,
            weekly_detail=weekly_detail,
        )


def _leave_rows(ctx: QuarterlyReportContext):
    return style.leave_rows(ctx.leave_breakdown)


def _leave_top2(ctx: QuarterlyReportContext) -> set[str]:
    return style.leave_top2(ctx.leave_breakdown)


def _period_volumes_table(ctx: QuarterlyReportContext):
    headers = [
        "Role",
        "RN Shifts",
        "PM Shifts",
        "Total Shifts",
        "Exceptions",
        "OT RN",
        "OT PM",
    ]
    col_w = style.full_width_col_widths([2.0, 0.9, 0.9, 1.0, 1.0, 0.85, 0.85])
    rows = [headers] + [list(r) for r in ctx.period_volumes]
    rows.append(list(ctx.period_vol_total))
    total_row = len(rows) - 1
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), style.NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), style.WHITE),
                ("FONTNAME", (0, 0), (-1, 0), style.F("BarlowBold")),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTNAME", (0, 1), (-1, -1), style.F("BarlowRegular")),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, total_row - 1),
                    [style.WHITE, style.LGRAY],
                ),
                ("GRID", (0, 0), (-1, -1), 0.5, style.MGRAY),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, total_row), (-1, total_row), style.MGRAY),
                (
                    "FONTNAME",
                    (0, total_row),
                    (-1, total_row),
                    style.F("IBMPlexMonoBold"),
                ),
            ]
            + style.num_style_cells([1, 2, 3, 4, 5, 6])
        )
    )
    return t


def _base_coverage_table(ctx: QuarterlyReportContext):
    return style.base_coverage_table(ctx.base_coverage, [1.8, 1.3, 1.3, 1.3, 1.8])


def _exceptions_table(ctx: QuarterlyReportContext):
    return style.exception_table(ctx.leave_breakdown)


def _weekly_detail_table(ctx: QuarterlyReportContext):
    headers = ["Week", "Staffing Rate", "OT Dependency", "Shift Exception %"]
    col_w = style.full_width_col_widths([1.5, 2.0, 2.0, 2.0])
    rows = [headers] + [list(r) for r in ctx.weekly_detail]
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), style.NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), style.WHITE),
                ("FONTNAME", (0, 0), (-1, 0), style.F("BarlowBold")),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("FONTNAME", (0, 1), (-1, -1), style.F("BarlowRegular")),
                ("FONTSIZE", (0, 1), (-1, -1), 8),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [style.WHITE, style.LGRAY]),
                ("GRID", (0, 0), (-1, -1), 0.5, style.MGRAY),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
            + style.num_style_cells([1, 2, 3])
        )
    )
    return t


def _build_trend_fig(ctx: QuarterlyReportContext):
    return style.trend_fig(
        ctx.weekly_trend,
        height_in=2.6,
        exception_label="Shift Exception % (left)",
        xtick_fontsize=6,
        xtick_rotation=30,
        xtick_ha="right",
    )


def _build_exception_bar_fig(ctx: QuarterlyReportContext):
    return style.exception_bar_fig(ctx.leave_breakdown)


def build_pdf(ctx: QuarterlyReportContext, output_path: str) -> str:
    style.register_fonts()
    running_header = f"Quarterly Staffing Report \u2014 {ctx.period}"
    on_first, on_later = style.make_page_callbacks(
        footer_short_title=f"Quarterly Staffing {ctx.period}",
        running_header_title=running_header,
    )

    doc = SimpleDocTemplate(
        output_path,
        pagesize=style.PAGE_SIZE,
        leftMargin=style.MARGIN,
        rightMargin=style.MARGIN,
        topMargin=style.MARGIN,
        bottomMargin=style.MARGIN,
    )

    story = [
        style.title_banner(
            f"QUARTERLY STAFFING REPORT \u2014 {ctx.period}",
            f"{ctx.dates}  |  {ctx.weeks_count}-Week Period",
            meta_line=f"Prepared {ctx.prepared_date} \u00b7 CONFIDENTIAL",
        ),
        Spacer(1, 10),
        style.section_bar("KEY PERFORMANCE INDICATORS"),
        style.kpi_row(ctx.kpi_data),
        Spacer(1, 10),
        style.section_bar("WEEKLY TREND"),
        style.chart_to_image(_build_trend_fig(ctx), style.USABLE_W),
        Spacer(1, 10),
        style.section_bar("EXCEPTION BREAKDOWN"),
        style.chart_to_image(_build_exception_bar_fig(ctx), style.USABLE_W, 1.8 * inch),
        Spacer(1, 10),
        style.section_bar("PERIOD VOLUMES"),
        _period_volumes_table(ctx),
        Spacer(1, 10),
        style.section_bar("COVERAGE BY BASE"),
        _base_coverage_table(ctx),
        Spacer(1, 10),
        style.section_bar("SCHEDULE EXCEPTIONS"),
        _exceptions_table(ctx),
        Spacer(1, 10),
        style.section_bar("WEEKLY DETAIL"),
        _weekly_detail_table(ctx),
        Spacer(1, 10),
    ]

    doc.build(
        story,
        onFirstPage=on_first,
        onLaterPages=on_later,
        canvasmaker=style.NumberedCanvas,
    )
    return output_path


def _output_path(output_dir: str, ctx: QuarterlyReportContext) -> str:
    os.makedirs(output_dir, exist_ok=True)
    yy = str(ctx.fy_label_year)[-2:]
    name = f"BMF_Quarterly_Staffing_FY{yy}Q{ctx.quarter}.pdf"
    return os.path.join(output_dir, name)


def export_quarterly_staffing_pdf(
    db_path: str,
    fy_label_year: int,
    quarter: int,
    output_dir: str,
) -> str:
    ctx = load_quarter_report_data(db_path, fy_label_year, quarter)
    path = _output_path(output_dir, ctx)
    return build_pdf(ctx, path)


# --------------------------------------------------------------------
# HTML export (board-ready, same visual family as the weekly report)
# --------------------------------------------------------------------

# KPI label -> whether a higher value is better (for prior-quarter deltas).
_KPI_DIRECTION = {
    "Avg Staffing Rate": True,
    "Avg OT Dependency": False,
    "Avg Shift Exception %": False,
    "Avg System RW %": True,
    "Avg System GR %": True,
}


def _parse_pct(value: str) -> float | None:
    try:
        return float(value.rstrip("%")) / 100.0
    except (ValueError, AttributeError):
        return None


def _kpis_with_deltas(
    ctx: QuarterlyReportContext, prior: QuarterlyReportContext | None
) -> list[tuple[str, str] | tuple[str, str, str]]:
    from staffing_tool import report_html as rh

    prior_by_label = dict(prior.kpi_data) if prior else {}
    kpis: list[tuple[str, str] | tuple[str, str, str]] = []
    for label, value in ctx.kpi_data:
        higher_better = _KPI_DIRECTION.get(label)
        cur = _parse_pct(value)
        prev = _parse_pct(prior_by_label.get(label, ""))
        if higher_better is None or cur is None or prev is None:
            kpis.append((label, value))
        else:
            kpis.append(
                (label, value, rh.delta_html(cur, prev, higher_is_better=higher_better))
            )
    return kpis


def build_html(
    ctx: QuarterlyReportContext,
    output_path: str,
    prior_ctx: QuarterlyReportContext | None = None,
) -> str:
    from staffing_tool import report_html as rh

    trend_b64 = rh.fig_to_png_base64(_build_trend_fig(ctx)) if ctx.weekly_trend else ""
    exc_b64 = rh.fig_to_png_base64(_build_exception_bar_fig(ctx))
    top2 = _leave_top2(ctx)

    kpi_note = (
        rh.note(f"Change shown vs {prior_ctx.period} (percentage points).")
        if prior_ctx
        else ""
    )
    body = rh.section_bar("KEY PERFORMANCE INDICATORS — QUARTER AVERAGES")
    body += rh.body_cell(rh.kpi_strip(_kpis_with_deltas(ctx, prior_ctx)) + kpi_note)

    if trend_b64:
        body += rh.section_bar("WEEKLY TREND THIS QUARTER")
        body += rh.body_cell(rh.chart_img(trend_b64, "Weekly staffing trend"))

    top2_note = ", ".join(
        f"{code} ({count})"
        for code, count in sorted(ctx.leave_breakdown, key=lambda r: r[1], reverse=True)
        if code in top2
    )
    grid_label = " &middot; ".join(EXCEPTION_GRID_COLS)
    body += rh.section_bar(f"EXCEPTION BREAKDOWN ({grid_label})")
    body += rh.body_cell(
        rh.chart_img(exc_b64, "Exception breakdown")
        + '<div style="height:12px;"></div>'
        + rh.exception_mix_table(ctx.leave_breakdown, top2)
        + rh.note(f"Top drivers (red in chart): {top2_note or 'n/a'}.")
    )

    body += rh.section_bar("PERIOD VOLUMES BY ROLE")
    vol_rows = [list(r) for r in ctx.period_volumes] + [list(ctx.period_vol_total)]
    body += rh.body_cell(
        rh.data_table(
            [
                "Role",
                "RN Shifts",
                "PM Shifts",
                "Total Shifts",
                "Exceptions",
                "OT RN",
                "OT PM",
            ],
            vol_rows,
            right_cols={1, 2, 3, 4, 5, 6},
            total_row=True,
        )
    )

    body += rh.section_bar("COVERAGE BY BASE")
    body += rh.body_cell(
        rh.data_table(
            ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"],
            [list(r) for r in ctx.base_coverage],
            right_cols={1, 2, 3, 4},
        )
    )

    body += rh.section_bar("WEEK-BY-WEEK DETAIL")
    body += rh.body_cell(
        rh.data_table(
            ["Week", "Staffing Rate", "OT Dependency", "Exception %"],
            [list(r) for r in ctx.weekly_detail],
            right_cols={1, 2, 3},
        )
    )

    html = rh.report_shell(
        title="QUARTERLY STAFFING REPORT",
        subtitle=f"{ctx.period} &nbsp;|&nbsp; {ctx.dates}",
        meta=(
            f"Weeks included: {ctx.weeks_count} &middot; "
            f"Prepared {ctx.prepared_date} &middot; CONFIDENTIAL"
        ),
        body=body,
        doc_title=f"Quarterly Staffing Report — {ctx.period}",
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def _prior_quarter(fy_label_year: int, quarter: int) -> tuple[int, int]:
    return (fy_label_year, quarter - 1) if quarter > 1 else (fy_label_year - 1, 4)


def export_quarterly_staffing_html(
    db_path: str,
    fy_label_year: int,
    quarter: int,
    output_dir: str,
) -> str:
    ctx = load_quarter_report_data(db_path, fy_label_year, quarter)
    prior_ctx = None
    try:
        prior_ctx = load_quarter_report_data(
            db_path, *_prior_quarter(fy_label_year, quarter)
        )
    except ValueError:
        pass  # first quarter with data — no comparison to show
    path = _output_path(output_dir, ctx).removesuffix(".pdf") + ".html"
    return build_html(ctx, path, prior_ctx)
