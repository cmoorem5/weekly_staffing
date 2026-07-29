"""
Shared visual style for BMF Clinical Operations PDF reports.
Canonical reference: docs/BMF_Visual_Style_Spec.md
Polish reference: output/BMF FY27 Clinical Ops Expansion.pdf
"""

import os

import matplotlib

from staffing_tool.paths import FONT_DIR as _FONT_DIR
from staffing_tool.paths import OUTPUT_DIR as _OUTPUT_DIR
from staffing_tool.paths import resolve_logo_path

matplotlib.use("Agg")
from io import BytesIO

import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Table, TableStyle

FONT_DIR = str(_FONT_DIR)
OUTPUT_DIR = str(_OUTPUT_DIR)

# ---------------------------------------------------------------------------
# BRAND COLORS
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#052C47")
BLUE = colors.HexColor("#2A4492")
LGRAY = colors.HexColor("#E6E6E6")
MGRAY = colors.HexColor("#CBC7D1")
RED = colors.HexColor("#C12126")
WHITE = colors.white
BLACK = colors.black

C_NAVY = "#052C47"
C_BLUE = "#2A4492"
C_LGRAY = "#E6E6E6"
C_MGRAY = "#CBC7D1"
C_RED = "#C12126"

# ---------------------------------------------------------------------------
# PAGE GEOMETRY
# ---------------------------------------------------------------------------
PAGE_SIZE = letter
MARGIN = 0.5 * inch
USABLE_W = 7.5 * inch
USABLE_H = 10.0 * inch

_FONT_MAP = {}
_FONTS_REGISTERED = False


def register_fonts():
    """Register Barlow + IBM Plex Mono when TTF files exist; else Helvetica/Courier."""
    global _FONT_MAP, _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    specs = [
        ("BarlowRegular", "Barlow-Regular.ttf", "Helvetica"),
        ("BarlowBold", "Barlow-Bold.ttf", "Helvetica-Bold"),
        ("BarlowSemiBold", "Barlow-SemiBold.ttf", "Helvetica"),
        ("BarlowCondensedBold", "BarlowCondensed-Bold.ttf", "Helvetica-Bold"),
        ("IBMPlexMonoRegular", "IBMPlexMono-Regular.ttf", "Courier"),
        ("IBMPlexMonoBold", "IBMPlexMono-Bold.ttf", "Courier-Bold"),
    ]
    for reg_name, filename, fallback in specs:
        path = os.path.join(FONT_DIR, filename)
        if os.path.isfile(path):
            if reg_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(reg_name, path))
            _FONT_MAP[reg_name] = reg_name
        else:
            _FONT_MAP[reg_name] = fallback

    barlow_regular = os.path.join(FONT_DIR, "Barlow-Regular.ttf")
    if os.path.isfile(barlow_regular):
        for fname in ("Barlow-Regular.ttf", "Barlow-Bold.ttf"):
            p = os.path.join(FONT_DIR, fname)
            if os.path.isfile(p):
                fm.fontManager.addfont(p)
        plt.rcParams.update(
            {
                "font.family": "Barlow",
                "font.sans-serif": ["Barlow", "Arial", "Helvetica"],
            }
        )
    else:
        plt.rcParams.update(
            {
                "font.family": "sans-serif",
                "font.sans-serif": ["Arial", "Helvetica"],
            }
        )

    _FONTS_REGISTERED = True


def F(name):
    """Resolve registered font name with fallback."""
    return _FONT_MAP.get(name, name)


# ---------------------------------------------------------------------------
# LAYOUT COMPONENTS
# ---------------------------------------------------------------------------


def section_bar(text):
    t = Table([[text]], colWidths=[USABLE_W])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, -1), WHITE),
                ("FONTNAME", (0, 0), (-1, -1), F("BarlowCondensedBold")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return t


LOGO_MAX_HEIGHT_PT = 64


def _logo_image(max_height_pt: float = LOGO_MAX_HEIGHT_PT):
    """BMF logo scaled to a max height, preserving aspect ratio; None if missing."""
    path = resolve_logo_path()
    if path is None:
        return None
    try:
        from PIL import Image as PILImage

        with PILImage.open(path) as im:
            w_px, h_px = im.size
    except (OSError, ImportError):
        return None
    if not h_px:
        return None
    height = max_height_pt
    width = height * (w_px / h_px)
    return Image(str(path), width=width, height=height)


def title_banner(title_text, subtitle_text, meta_line=None):
    """Navy cover banner with BMF branding — matches Expansion report polish."""
    rows = [[title_text], [subtitle_text]]
    if meta_line:
        rows.append([meta_line])
    rows.extend([["BOSTON MEDFLIGHT"], ["CLINICAL OPERATIONS"]])

    brand_start = len(rows) - 2
    t = Table(rows, colWidths=[USABLE_W])
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        # title
        ("TEXTCOLOR", (0, 0), (0, 0), WHITE),
        ("FONTNAME", (0, 0), (0, 0), F("BarlowCondensedBold")),
        ("FONTSIZE", (0, 0), (0, 0), 18),
        ("TOPPADDING", (0, 0), (0, 0), 14),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        # subtitle
        ("TEXTCOLOR", (0, 1), (0, 1), LGRAY),
        ("FONTNAME", (0, 1), (0, 1), F("BarlowRegular")),
        ("FONTSIZE", (0, 1), (0, 1), 10),
        ("TOPPADDING", (0, 1), (0, 1), 0),
        ("BOTTOMPADDING", (0, 1), (0, 1), 6 if meta_line else 10),
    ]
    if meta_line:
        style += [
            ("TEXTCOLOR", (0, 2), (0, 2), LGRAY),
            ("FONTNAME", (0, 2), (0, 2), F("BarlowRegular")),
            ("FONTSIZE", (0, 2), (0, 2), 8),
            ("TOPPADDING", (0, 2), (0, 2), 0),
            ("BOTTOMPADDING", (0, 2), (0, 2), 10),
        ]
    style += [
        ("TEXTCOLOR", (0, brand_start), (0, brand_start), WHITE),
        ("FONTNAME", (0, brand_start), (0, brand_start), F("BarlowCondensedBold")),
        ("FONTSIZE", (0, brand_start), (0, brand_start), 8),
        ("TOPPADDING", (0, brand_start), (0, brand_start), 4),
        ("BOTTOMPADDING", (0, brand_start), (0, brand_start), 0),
        ("TEXTCOLOR", (0, brand_start + 1), (0, brand_start + 1), LGRAY),
        (
            "FONTNAME",
            (0, brand_start + 1),
            (0, brand_start + 1),
            F("BarlowCondensedBold"),
        ),
        ("FONTSIZE", (0, brand_start + 1), (0, brand_start + 1), 8),
        ("TOPPADDING", (0, brand_start + 1), (0, brand_start + 1), 0),
        ("BOTTOMPADDING", (0, brand_start + 1), (0, brand_start + 1), 14),
    ]
    t.setStyle(TableStyle(style))

    logo = _logo_image()
    if logo is None:
        return t

    outer = Table(
        [[t, logo]], colWidths=[USABLE_W - logo.drawWidth - 12, logo.drawWidth + 12]
    )
    outer.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 12),
            ]
        )
    )
    return outer


def kpi_row(kpi_list):
    n = len(kpi_list)
    col_w = USABLE_W / n
    values = [[v for _, v in kpi_list]]
    labels = [[label for label, _ in kpi_list]]
    t = Table(
        values + labels, colWidths=[col_w] * n, rowHeights=[0.55 * inch, 0.3 * inch]
    )
    # Vertical dividers only — INNERGRID also draws a horizontal rule that cuts through values.
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), WHITE),
        ("BOX", (0, 0), (-1, -1), 0.5, MGRAY),
        ("FONTNAME", (0, 0), (-1, 0), F("IBMPlexMonoBold")),
        ("FONTSIZE", (0, 0), (-1, 0), 20),
        ("TEXTCOLOR", (0, 0), (-1, 0), NAVY),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
        ("FONTNAME", (0, 1), (-1, 1), F("BarlowRegular")),
        ("FONTSIZE", (0, 1), (-1, 1), 8),
        ("TEXTCOLOR", (0, 1), (-1, 1), BLACK),
        ("ALIGN", (0, 1), (-1, 1), "CENTER"),
        ("VALIGN", (0, 1), (-1, 1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]
    for col in range(1, n):
        style.append(("LINEBEFORE", (col, 0), (col, -1), 0.5, MGRAY))
    t.setStyle(TableStyle(style))
    return t


def num_style_cells(col_indices, start_row=1, end_row=-1):
    return [
        ("FONTNAME", (col, start_row), (col, end_row), F("IBMPlexMonoRegular"))
        for col in col_indices
    ] + [("ALIGN", (col, start_row), (col, end_row), "RIGHT") for col in col_indices]


# ---------------------------------------------------------------------------
# CHART HELPERS
# ---------------------------------------------------------------------------


def base_figure(w_in, h_in):
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(C_MGRAY)
    ax.spines["bottom"].set_color(C_MGRAY)
    ax.tick_params(colors="#333333", labelsize=7)
    ax.xaxis.label.set_fontsize(7)
    ax.yaxis.label.set_fontsize(7)
    return fig, ax


def full_width_col_widths(relative: list[float]) -> list[float]:
    """Scale column weight ratios to span USABLE_W (match section_bar width)."""
    total = sum(relative)
    return [USABLE_W * w / total for w in relative]


def chart_to_image(fig, width_in_doc, height_in_doc=None):
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    img = Image(buf, width=width_in_doc)
    if height_in_doc is not None:
        img.drawHeight = height_in_doc
    return img


def apply_below_chart_legend(fig, *axes, ncol: int = 3) -> None:
    """Combined legend under the plot (dual-axis trend charts)."""
    handles: list = []
    labels: list[str] = []
    for ax in axes:
        h, lab = ax.get_legend_handles_labels()
        handles.extend(h)
        labels.extend(lab)
    fig.subplots_adjust(bottom=0.16)
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.04),
        ncol=ncol,
        fontsize=6,
        framealpha=0.95,
        edgecolor=C_MGRAY,
        facecolor="white",
    )


# ---------------------------------------------------------------------------
# PAGE CHROME — running header, footer, page X of Y (Expansion report pattern)
# ---------------------------------------------------------------------------


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas so footers can show 'Page X of Y'."""

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._page_states = []

    def showPage(self):
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._page_states)
        for state in self._page_states:
            self.__dict__.update(state)
            self._draw_page_number(total)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _draw_page_number(self, page_count):
        if hasattr(self, "_page_number_draw"):
            self._page_number_draw(self, page_count)


def make_page_callbacks(footer_short_title, running_header_title):
    """
    footer_short_title: e.g. 'Weekly Staffing'
    running_header_title: e.g. 'Weekly Staffing Report — Week of June 9, 2026'
    """

    def _footer_text(page_num, page_count):
        return (
            f"Boston MedFlight \u00b7 {footer_short_title} \u00b7 "
            f"Confidential \u00b7 Page {page_num} of {page_count}"
        )

    def _draw_footer(c, page_count):
        c.saveState()
        c.setFont(F("BarlowRegular"), 7)
        c.setFillColor(MGRAY)
        text = _footer_text(c.getPageNumber(), page_count)
        c.drawCentredString(PAGE_SIZE[0] / 2, 0.35 * inch, text)
        c.restoreState()

    def _draw_running_header(c):
        c.saveState()
        w, h = PAGE_SIZE
        c.setFont(F("BarlowCondensedBold"), 7)
        c.setFillColor(NAVY)
        header = f"BOSTON MEDFLIGHT \u00b7 {running_header_title.upper()} \u00b7 CONFIDENTIAL"
        c.drawString(MARGIN, h - 0.40 * inch, header)
        c.setStrokeColor(MGRAY)
        c.setLineWidth(0.5)
        c.line(MARGIN, h - 0.46 * inch, w - MARGIN, h - 0.46 * inch)
        c.restoreState()

    def on_first_page(c, doc):
        def draw(cnv, total):
            _draw_footer(cnv, total)

        c._page_number_draw = draw

    def on_later_pages(c, doc):
        def draw(cnv, total):
            _draw_running_header(cnv)
            _draw_footer(cnv, total)

        c._page_number_draw = draw

    return on_first_page, on_later_pages


# ---------------------------------------------------------------------
# Shared report content: helpers used by both the weekly and quarterly
# builders. These were duplicated in weekly_pdf_report and
# quarterly_pdf_report; the only per-report differences are passed in as
# arguments (column widths, figure height, a legend label, tick styling).
# tests/test_report_shared_helpers.py pins the rendered result.
# ---------------------------------------------------------------------

EM_DASH = "—"


def pct(value: float) -> str:
    """Fraction to a one-decimal percentage, e.g. 0.9123 -> '91.2%'."""
    return f"{100 * value:.1f}%"


def short_label(iso: str) -> str:
    """'2025-12-07' -> 'Dec 7' for compact chart axes."""
    from datetime import datetime

    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return d.strftime("%b ") + str(d.day)


def leave_rows(leave_breakdown: list[tuple[str, int]]):
    """(code, count, share) rows plus the total, from a leave breakdown."""
    total = sum(count for _code, count in leave_breakdown)
    rows = [
        (code, count, f"{100 * count / total:.1f}%" if total else EM_DASH)
        for code, count in leave_breakdown
    ]
    return rows, total


def leave_top2(leave_breakdown: list[tuple[str, int]]) -> set[str]:
    """The two most-used exception codes (ignoring zero counts)."""
    ranked = sorted(leave_breakdown, key=lambda r: r[1], reverse=True)
    return {code for code, count in ranked[:2] if count > 0}


# Shared opening commands for every data table: navy header band, Barlow
# body text, zebra banding, grid, padding. Callers append their own
# alignment/emphasis commands.
def data_table_style(header_row_only=False) -> list:
    return [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), F("BarlowBold")),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTNAME", (0, 1), (-1, -1), F("BarlowRegular")),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, MGRAY),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]


def exception_table(
    leave_breakdown: list[tuple[str, int]],
    col_ratios: list[float] | None = None,
) -> Table:
    """Exception-type table with a totals row and the top two codes in red."""
    headers = ["Exception Type", "Count", "% of Total"]
    col_w = full_width_col_widths(col_ratios or [4.0, 1.5, 2.0])
    rows_data, total = leave_rows(leave_breakdown)
    rows = [headers] + [[code, str(count), share] for code, count, share in rows_data]
    rows.append(["Total", str(total), "100%" if total else EM_DASH])
    total_row = len(rows) - 1

    top2 = leave_top2(leave_breakdown)
    red_rules = []
    for i, (code, _count, _share) in enumerate(rows_data, start=1):
        if code in top2:
            red_rules += [
                ("TEXTCOLOR", (1, i), (2, i), RED),
                ("FONTNAME", (1, i), (2, i), F("IBMPlexMonoBold")),
            ]

    t = Table(rows, colWidths=col_w)
    t.setStyle(
        TableStyle(
            data_table_style()
            + [
                ("ROWBACKGROUNDS", (0, 1), (-1, total_row - 1), [WHITE, LGRAY]),
                ("ALIGN", (1, 0), (2, -1), "CENTER"),
                ("BACKGROUND", (0, total_row), (-1, total_row), MGRAY),
                ("FONTNAME", (0, total_row), (-1, total_row), F("IBMPlexMonoBold")),
            ]
            + num_style_cells([1, 2])
            + red_rules
        )
    )
    return t


def base_coverage_table(
    base_coverage: list[tuple[str, str, str, str, str]],
    col_ratios: list[float],
) -> Table:
    """Per-base RW/GR shift counts and availability percentages."""
    headers = ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"]
    rows = [headers] + [list(r) for r in base_coverage]
    t = Table(rows, colWidths=full_width_col_widths(col_ratios))
    t.setStyle(
        TableStyle(
            data_table_style()
            + [
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LGRAY]),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ]
            + num_style_cells([1, 2, 3, 4])
        )
    )
    return t


def trend_fig(
    trend: list[tuple[str, float, float, float]],
    *,
    height_in: float,
    exception_label: str,
    xtick_fontsize: int = 7,
    xtick_rotation: int = 0,
    xtick_ha: str = "center",
):
    """Staffing / exception bars plus an OT-dependency line on a twin axis."""
    import matplotlib.ticker as mticker

    labels = [r[0] for r in trend]
    staffing = [r[1] for r in trend]
    ot_dep = [r[2] for r in trend]
    exc_pct = [r[3] for r in trend]
    x = range(len(labels))

    fig, ax1 = plt.subplots(figsize=(7.5, height_in))
    fig.patch.set_facecolor("white")
    ax1.set_facecolor("white")
    ax1.bar(
        x,
        exc_pct,
        color=C_MGRAY,
        width=0.55,
        alpha=0.55,
        label=exception_label,
        zorder=1,
    )
    ax1.plot(
        x,
        staffing,
        color=C_BLUE,
        linewidth=2,
        marker="o",
        markersize=4,
        label="Staffing Rate % (left)",
        zorder=3,
    )
    ax1.set_ylabel("Staffing / Exception %", fontsize=7, color="#333333")
    ax1.set_ylim(0, 110)
    ax1.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    ax2 = ax1.twinx()
    ax2.plot(
        x,
        ot_dep,
        color=C_RED,
        linewidth=1.5,
        marker="s",
        markersize=3,
        linestyle="--",
        label="OT Dependency % (right)",
        zorder=3,
    )
    ax2.set_ylabel("OT Dependency %", fontsize=7, color=C_RED)
    ax2.set_ylim(0, 30)
    ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))
    ax2.spines["right"].set_color(C_RED)
    ax2.tick_params(axis="y", colors=C_RED, labelsize=7)

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(
        labels, fontsize=xtick_fontsize, rotation=xtick_rotation, ha=xtick_ha
    )
    ax1.spines["top"].set_visible(False)
    ax1.spines["left"].set_color(C_MGRAY)
    ax1.spines["bottom"].set_color(C_MGRAY)
    ax1.tick_params(colors="#333333", labelsize=7)
    ax1.yaxis.grid(True, color=C_MGRAY, linewidth=0.5, linestyle="--")
    ax1.set_axisbelow(True)

    apply_below_chart_legend(fig, ax1, ax2)
    fig.tight_layout(pad=0.3, rect=(0, 0.10, 1, 1))
    return fig


def exception_bar_fig(leave_breakdown: list[tuple[str, int]]):
    """Horizontal exception-count bars, top two codes in red."""
    import matplotlib.ticker as mticker

    codes = [code for code, _count in leave_breakdown]
    counts = [count for _code, count in leave_breakdown]
    top2 = leave_top2(leave_breakdown)
    bar_colors = [C_RED if code in top2 else C_BLUE for code in codes]

    fig, ax = base_figure(7.5, 1.8)
    y = range(len(codes))
    ax.barh(list(y), counts, color=bar_colors, height=0.5)
    ax.set_yticks(list(y))
    ax.set_yticklabels(codes, fontsize=7)
    ax.set_xlabel("Shift exceptions (count)", fontsize=7, color="#333333")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.xaxis.grid(True, color=C_MGRAY, linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    for i, v in enumerate(counts):
        if v:
            ax.text(v + 0.1, i, str(v), va="center", fontsize=7, color="#333333")
    fig.tight_layout(pad=0.4)
    return fig
