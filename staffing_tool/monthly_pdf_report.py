"""
Monthly staffing PDF report builder (data from staffing.db).

Reuses the data loader from monthly_html_report.py (MonthlyBoardData /
load_monthly_board_data) and the shared PDF chart builders from
quarterly_pdf_report.py — the weekly trend / exception breakdown charts read
only .weekly_trend / .leave_breakdown, which MonthlyBoardData already
provides in the same shape. Visual style: staffing_tool/report_style.py.

The KPI-delta-vs-prior-period and combined exception chart+table layout
below mirror monthly_html_report.py's board summary so the PDF and HTML
board reports read as the same document in two formats.
"""

from __future__ import annotations

import os
from datetime import date

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from staffing_tool import report_style as style
from staffing_tool.monthly_html_report import MonthlyBoardData, load_monthly_board_data

GREEN = colors.HexColor("#0F6E56")
NEUTRAL = colors.HexColor("#666666")

# label -> whether a higher value is better, for KPI deltas vs the prior period.
_KPI_SPEC = [
    ("Staffing Rate", "avg_staffing_rate", True),
    ("Day Fill", "avg_day_staffing_rate", True),
    ("Night Fill", "avg_night_staffing_rate", True),
    ("OT Dependency", "avg_ot_dependency", False),
    ("Shift Exception %", "avg_leave_exposure", False),
    ("System RW %", "avg_system_rw_pct", True),
    ("System GR %", "avg_system_gr_pct", True),
]


def _kpi_delta(
    current: float, prior: float, higher_is_better: bool
) -> tuple[str, colors.Color]:
    """+/- change in percentage points vs the prior period, with a good/bad color.

    Plain +/- signs, not unicode triangle glyphs: the Barlow/IBM Plex Mono
    TTFs embedded in this report don't include U+25B2/25BC/25B6, so those
    would render as missing-glyph boxes.
    """
    diff = round((current - prior) * 100, 1)
    if abs(diff) < 0.05:
        return "± 0.0 pts", NEUTRAL
    up = diff > 0
    good = up == higher_is_better
    sign = "+" if up else "-"
    color = GREEN if good else style.RED
    return f"{sign} {abs(diff):.1f} pts", color


def _kpi_rows(data: MonthlyBoardData) -> list[tuple[str, str, str, colors.Color]]:
    """(label, value, delta text, delta color) — delta blank when there's no prior period."""
    r, p = data.rollups, data.prior_rollups
    rows: list[tuple[str, str, str, colors.Color]] = []
    for label, attr, higher_better in _KPI_SPEC:
        value = getattr(r, attr)
        if p is not None:
            delta_text, color = _kpi_delta(value, getattr(p, attr), higher_better)
        else:
            delta_text, color = "", style.BLACK
        rows.append((label, style.pct(value), delta_text, color))
    return rows


def _kpi_table(rows: list[tuple[str, str, str, colors.Color]]) -> Table:
    n = len(rows)
    col_w = style.USABLE_W / n
    values = [[v for _, v, _, _ in rows]]
    deltas = [[d for _, _, d, _ in rows]]
    labels = [[label for label, _, _, _ in rows]]
    t = Table(
        values + deltas + labels,
        colWidths=[col_w] * n,
        rowHeights=[0.5 * inch, 0.22 * inch, 0.28 * inch],
    )
    cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), style.WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, style.MGRAY),
        ("FONTNAME", (0, 0), (-1, 0), style.F("IBMPlexMonoBold")),
        ("FONTSIZE", (0, 0), (-1, 0), 18),
        ("TEXTCOLOR", (0, 0), (-1, 0), style.NAVY),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, 1), style.F("IBMPlexMonoBold")),
        ("FONTSIZE", (0, 1), (-1, 1), 7.5),
        ("ALIGN", (0, 1), (-1, 1), "CENTER"),
        ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
        ("FONTNAME", (0, 2), (-1, 2), style.F("BarlowRegular")),
        ("FONTSIZE", (0, 2), (-1, 2), 8),
        ("TEXTCOLOR", (0, 2), (-1, 2), style.BLACK),
        ("ALIGN", (0, 2), (-1, 2), "CENTER"),
        ("VALIGN", (0, 2), (-1, 2), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("TOPPADDING", (0, 1), (-1, 1), 0),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
        ("TOPPADDING", (0, 2), (-1, 2), 1),
        ("BOTTOMPADDING", (0, 2), (-1, 2), 5),
    ]
    for col in range(1, n):
        cmds.append(("LINEBEFORE", (col, 0), (col, -1), 0.5, style.MGRAY))
    for col, (_, _, _, color) in enumerate(rows):
        cmds.append(("TEXTCOLOR", (col, 1), (col, 1), color))
    t.setStyle(TableStyle(cmds))
    return t


def _note(text: str) -> Paragraph:
    style_ = ParagraphStyle(
        "MonthlyPdfNote",
        fontName=style.F("BarlowRegular"),
        fontSize=7.5,
        textColor=NEUTRAL,
    )
    return Paragraph(text, style_)


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

    kpi_note = (
        f"Change shown vs prior period ({data.prior_label}), percentage points."
        if data.prior_rollups
        else "No prior-period data available for comparison."
    )

    story = [
        style.title_banner(
            "MONTHLY STAFFING REPORT",
            f"{period}  |  {data.weeks_count}-Week Period",
            meta_line=f"Prepared {date.today():%B %d, %Y} · CONFIDENTIAL",
        ),
        Spacer(1, 10),
        style.section_bar("KEY PERFORMANCE INDICATORS"),
        _kpi_table(_kpi_rows(data)),
        Spacer(1, 3),
        _note(kpi_note),
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
        Spacer(1, 8),
        style.exception_table(data.leave_breakdown),
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
