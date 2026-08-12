"""
Monthly staffing PDF report builder (data from staffing.db).

Reuses the data loader from monthly_html_report.py (MonthlyBoardData /
load_monthly_board_data) and the shared PDF chart builders from
quarterly_pdf_report.py — the weekly trend / exception breakdown charts read
only .weekly_trend / .leave_breakdown, which MonthlyBoardData already
provides in the same shape. Visual style: staffing_tool/report_style.py.
"""

from __future__ import annotations

import os
from datetime import date

from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from staffing_tool import report_style as style
from staffing_tool.monthly_html_report import MonthlyBoardData, load_monthly_board_data


def _kpi_data(data: MonthlyBoardData) -> list[tuple[str, str]]:
    r = data.rollups
    return [
        ("Staffing Rate", style.pct(r.avg_staffing_rate)),
        ("Day Fill", style.pct(r.avg_day_staffing_rate)),
        ("Night Fill", style.pct(r.avg_night_staffing_rate)),
        ("OT Dependency", style.pct(r.avg_ot_dependency)),
        ("Shift Exception %", style.pct(r.avg_leave_exposure)),
        ("System RW %", style.pct(r.avg_system_rw_pct)),
        ("System GR %", style.pct(r.avg_system_gr_pct)),
    ]


def _role_fill_table(data: MonthlyBoardData) -> Table:
    headers = ["Role", "Worked", "Capacity", "Fill Rate"]
    col_w = style.full_width_col_widths([2.0, 1.5, 1.5, 1.5])
    rows = [headers] + [
        [rf.label, str(rf.worked), str(rf.capacity), style.pct(rf.rate)]
        for rf in data.role_fill
    ]
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            style.data_table_style()
            + [
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [style.WHITE, style.LGRAY]),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
            + style.num_style_cells([1, 2, 3])
        )
    )
    return t


def _ot_table(data: MonthlyBoardData) -> Table:
    headers = ["Role", "OT Shifts"]
    total = sum(v for _, v in data.ot_by_role)
    rows = [headers] + [[label, str(v)] for label, v in data.ot_by_role]
    rows.append(["Total", str(total)])
    total_row = len(rows) - 1
    col_w = style.full_width_col_widths([3.0, 1.5])
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            style.data_table_style()
            + [
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, total_row - 1),
                    [style.WHITE, style.LGRAY],
                ),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("BACKGROUND", (0, total_row), (-1, total_row), style.MGRAY),
                (
                    "FONTNAME",
                    (0, total_row),
                    (-1, total_row),
                    style.F("IBMPlexMonoBold"),
                ),
            ]
            + style.num_style_cells([1])
        )
    )
    return t


def _weekly_detail_table(data: MonthlyBoardData) -> Table:
    headers = ["Week", "Staffing Rate", "OT Dependency", "Exception %", "Vacancies"]
    col_w = style.full_width_col_widths([1.3, 1.7, 1.7, 1.7, 1.3])
    rows = [headers] + [list(r) for r in data.weekly_detail]
    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            style.data_table_style()
            + [
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [style.WHITE, style.LGRAY]),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
            + style.num_style_cells([1, 2, 3, 4])
        )
    )
    return t


def build_monthly_pdf(data: MonthlyBoardData, output_path: str) -> str:
    from staffing_tool.quarterly_pdf_report import (
        _build_exception_bar_fig,
        _build_trend_fig,
    )

    style.register_fonts()
    period = f"{data.date_start} to {data.date_end}"
    on_first, on_later = style.make_page_callbacks(
        footer_short_title="Monthly Staffing",
        running_header_title=f"Monthly Staffing Report — {period}",
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
            "MONTHLY STAFFING REPORT",
            f"{period}  |  {data.weeks_count}-Week Period",
            meta_line=f"Prepared {date.today():%B %d, %Y} · CONFIDENTIAL",
        ),
        Spacer(1, 10),
        style.section_bar("KEY PERFORMANCE INDICATORS"),
        style.kpi_row(_kpi_data(data)),
        Spacer(1, 10),
    ]

    if data.weekly_trend:
        story += [
            style.section_bar("WEEKLY TREND"),
            style.chart_to_image(_build_trend_fig(data), style.USABLE_W),
            Spacer(1, 10),
        ]

    story += [
        style.section_bar("EXCEPTION BREAKDOWN"),
        style.chart_to_image(
            _build_exception_bar_fig(data), style.USABLE_W, 1.8 * inch
        ),
        Spacer(1, 10),
    ]

    if any(rf.worked for rf in data.role_fill):
        story += [
            style.section_bar("FILL RATE BY ROLE"),
            _role_fill_table(data),
            Spacer(1, 10),
        ]

    story += [
        style.section_bar("OVERTIME BY ROLE"),
        _ot_table(data),
        Spacer(1, 10),
        style.section_bar("COVERAGE BY BASE"),
        style.base_coverage_table(data.base_coverage, [1.8, 1.3, 1.3, 1.3, 1.8]),
        Spacer(1, 10),
        style.section_bar("SCHEDULE EXCEPTIONS"),
        style.exception_table(data.leave_breakdown),
        Spacer(1, 10),
        style.section_bar("WEEK-BY-WEEK DETAIL"),
        _weekly_detail_table(data),
        Spacer(1, 10),
    ]

    doc.build(
        story,
        onFirstPage=on_first,
        onLaterPages=on_later,
        canvasmaker=style.NumberedCanvas,
    )
    return output_path


def _output_path(output_dir: str, data: MonthlyBoardData) -> str:
    os.makedirs(output_dir, exist_ok=True)
    safe_start = data.date_start.replace("-", "")
    safe_end = data.date_end.replace("-", "")
    return os.path.join(output_dir, f"Monthly_staffing_{safe_start}_to_{safe_end}.pdf")


def export_monthly_report_pdf(
    db_path: str,
    date_start: str,
    date_end: str,
    output_dir: str,
) -> str:
    data = load_monthly_board_data(db_path, date_start, date_end)
    path = _output_path(output_dir, data)
    return build_monthly_pdf(data, path)
