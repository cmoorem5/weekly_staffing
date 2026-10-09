"""
Shared visual style for BMF Clinical Operations PDF reports.
Canonical reference: docs/BMF_Visual_Style_Spec.md
Polish reference: output/BMF FY27 Clinical Ops Expansion.pdf
"""

import math
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
from reportlab.lib.utils import ImageReader
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


def kpi_row(kpi_list, statuses: dict[str, tuple[str, str]] | None = None):
    """KPI tiles: value over label, plus a status line when ``statuses`` has one.

    ``statuses`` maps a tile label to (status text, hex color) from
    ``report_data.kpi_tile_statuses``; the color also tints a bar across the
    top of that tile so a reader scans good/bad before reading numbers.
    """
    n = len(kpi_list)
    col_w = USABLE_W / n
    statuses = statuses or {}
    values = [[v for _, v in kpi_list]]
    labels = [[label for label, _ in kpi_list]]
    rows = values + labels
    heights = [0.55 * inch, 0.3 * inch]
    if statuses:
        rows.append([statuses.get(label, ("", ""))[0] for label, _ in kpi_list])
        heights.append(0.22 * inch)
    t = Table(rows, colWidths=[col_w] * n, rowHeights=heights)
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
    if statuses:
        style += [
            ("FONTNAME", (0, 2), (-1, 2), F("BarlowBold")),
            ("FONTSIZE", (0, 2), (-1, 2), 7),
            ("ALIGN", (0, 2), (-1, 2), "CENTER"),
            ("VALIGN", (0, 2), (-1, 2), "TOP"),
            ("TOPPADDING", (0, 2), (-1, 2), 0),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
        ]
        for col, (label, _v) in enumerate(kpi_list):
            if label in statuses:
                c = colors.HexColor(statuses[label][1])
                style.append(("TEXTCOLOR", (col, 2), (col, 2), c))
                style.append(("LINEABOVE", (col, 0), (col, 0), 3, c))
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
    """Embed a matplotlib figure at ``width_in_doc``, keeping its aspect ratio.

    reportlab's ``Image(width=...)`` alone takes the PNG's pixel height as the
    height in points, which drew a 7.5 x 2.6 in trend chart about 5.4 in tall
    (vertically stretched ~2x). Height now follows the rendered aspect unless
    ``height_in_doc`` is given explicitly.
    """
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    px_w, px_h = ImageReader(buf).getSize()
    buf.seek(0)
    if height_in_doc is None:
        height_in_doc = width_in_doc * px_h / px_w
    return Image(buf, width=width_in_doc, height=height_in_doc)


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
def data_table_style() -> list:
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


# One panel per KPI: (title, tuple index, KPI threshold name, higher is better,
# axis floor/ceiling). Each panel gets its own y-range so a 88-95% staffing swing
# or a 4-6% exception swing is visible instead of flattened onto a 0-100 scale.
TREND_PANELS = (
    ("Staffing rate", 1, "Staffing Rate", True),
    ("OT dependency", 2, "OT Dependency", False),
    ("Shift exception %", 3, "Shift Exception %", False),
)
C_PRIOR = "#B8B8C0"
C_INK = "#1F2430"
C_MUTED = "#6B7280"


def _panel_range(values: list[float], *, higher_is_better: bool) -> tuple[float, float]:
    """Zoomed y-range around the data (percent scale), on a 2-point grid.

    Higher-is-better rates zoom in under a 100% ceiling; lower-is-better ones
    start at zero. Either way the window is at least 10 points tall so
    week-to-week noise isn't magnified into a dramatic slope.
    """
    if not values:
        return (0.0, 100.0) if higher_is_better else (0.0, 10.0)
    lo, hi = min(values), max(values)
    if higher_is_better:
        top = (
            min(100.0, math.ceil((hi + 2) / 2) * 2)
            if hi <= 100
            else math.ceil(hi / 2) * 2
        )
        bottom = max(0.0, math.floor((lo - 2) / 2) * 2)
        if top - bottom < 10:
            bottom = max(0.0, top - 10)
        return bottom, top
    top = max(10.0, math.ceil((hi + 2) / 2) * 2)
    return 0.0, top


def _target_text(fraction: float | None, higher_is_better: bool) -> str:
    if fraction is None:
        return ""
    return f"target {'≥' if higher_is_better else '≤'} {100.0 * fraction:.0f}%"


def _thin_ticks(n: int, max_ticks: int = 4) -> list[int]:
    """Evenly spaced indices to label on a narrow panel, always first and last."""
    if n <= max_ticks:
        return list(range(n))
    return sorted({round(k * (n - 1) / (max_ticks - 1)) for k in range(max_ticks)})


def trend_fig(
    trend: list[tuple[str, float, float, float]],
    *,
    height_in: float = 2.5,
    exception_label: str = "Shift exception %",
    xtick_fontsize: int = 7,
    xtick_rotation: int = 0,
    xtick_ha: str = "center",
    targets: dict[str, float] | None = None,
    prior: list[tuple[str, float, float, float]] | None = None,
    current_label: str = "",
    prior_label: str = "",
):
    """KPI trend as three side-by-side panels: staffing, OT, shift exceptions.

    ``trend`` rows are (label, staffing %, OT %, exception %) on a 0-100 scale.
    Each panel has its own zoomed y-axis (never a second axis on one panel),
    the KPI target named in the panel title and drawn as a dashed line when it
    falls inside the zoomed range, and the latest value printed at the end of
    the line. ``prior`` (same shape, aligned by position) is drawn as a gray
    line behind the current one, for the prior-year comparison; a legend
    appears only then, since a single series needs none.
    """
    import matplotlib.ticker as mticker

    targets = targets or {}
    labels = [r[0] for r in trend]
    x = list(range(len(labels)))
    titles = {3: exception_label}

    fig, axes = plt.subplots(1, 3, figsize=(7.5, height_in))
    fig.patch.set_facecolor("white")
    for ax, (title, idx, metric, higher) in zip(axes, TREND_PANELS, strict=True):
        vals = [float(r[idx]) for r in trend]
        prior_vals = [float(r[idx]) for r in (prior or [])][: len(vals)]
        target = targets.get(metric)
        lo, hi = _panel_range(
            [v for v in vals + prior_vals if not math.isnan(v)],
            higher_is_better=higher,
        )

        if prior_vals:
            ax.plot(
                x[: len(prior_vals)],
                prior_vals,
                color=C_PRIOR,
                linewidth=1.5,
                label=prior_label or "Prior",
                zorder=2,
            )
        ax.plot(
            x,
            vals,
            color=C_BLUE,
            linewidth=2,
            marker="o",
            markersize=3.5,
            label=current_label or title,
            zorder=3,
        )
        if target is not None and lo <= 100.0 * target <= hi:
            ax.axhline(
                100.0 * target,
                color=C_MUTED,
                linewidth=1,
                linestyle=(0, (3, 3)),
                label=f"{metric} target",
                zorder=1,
            )
        if vals:
            ax.annotate(
                f"{vals[-1]:.1f}%",
                (x[-1], vals[-1]),
                xytext=(4, 0),
                textcoords="offset points",
                fontsize=7,
                color=C_INK,
                fontweight="bold",
                va="center",
                annotation_clip=False,
            )

        ax.set_ylim(lo, hi)
        ax.set_xlim(-0.5, max(len(x) - 0.5, 0.5) + 0.6)
        name = titles.get(idx, title)
        ax.set_title(
            name, fontsize=8, color=C_INK, loc="left", fontweight="bold", pad=10
        )
        tgt = _target_text(target, higher)
        if tgt:
            ax.text(0, 1.01, tgt, transform=ax.transAxes, fontsize=6.5, color=C_MUTED)
        ticks = _thin_ticks(len(x))
        ax.set_xticks([x[i] for i in ticks])
        ax.set_xticklabels(
            [labels[i] for i in ticks],
            fontsize=xtick_fontsize - 0.5,
            rotation=xtick_rotation,
            ha=xtick_ha,
            color=C_MUTED,
        )
        ax.set_facecolor("white")
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(C_MGRAY)
        ax.tick_params(axis="y", labelsize=6.5, colors=C_MUTED, length=0)
        ax.tick_params(axis="x", length=0)
        ax.yaxis.grid(True, color="#ECECF0", linewidth=0.8)
        ax.set_axisbelow(True)
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.0f%%"))

    if prior:
        handles = [
            plt.Line2D([], [], color=C_BLUE, linewidth=2, marker="o", markersize=3.5),
            plt.Line2D([], [], color=C_PRIOR, linewidth=1.5),
        ]
        fig.legend(
            handles,
            [current_label or "Current", prior_label or "Prior"],
            loc="lower center",
            ncol=2,
            fontsize=7,
            frameon=False,
        )
        fig.tight_layout(pad=0.3, w_pad=1.6, rect=(0, 0.08, 1, 1))
    else:
        fig.tight_layout(pad=0.3, w_pad=1.6)
    return fig


def exception_bar_fig(leave_breakdown: list[tuple[str, int]]):
    """Horizontal exception-count bars, largest first, labeled count and share.

    One color for every bar: the ranking and the labels carry "what drives
    exceptions", where the old red-for-top-two read as "these codes are bad".
    """
    import matplotlib.ticker as mticker

    rows = sorted(leave_breakdown, key=lambda r: r[1], reverse=True)
    total = sum(count for _code, count in rows)
    codes = [code for code, _count in rows]
    counts = [count for _code, count in rows]

    fig, ax = base_figure(7.5, 1.8)
    y = list(range(len(codes)))
    ax.barh(y, counts, color=C_BLUE, height=0.55)
    ax.set_yticks(y)
    ax.set_yticklabels(codes, fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("Shift exceptions (count)", fontsize=7, color="#333333")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.xaxis.grid(True, color=C_MGRAY, linewidth=0.5, linestyle="--")
    ax.set_axisbelow(True)
    peak = max(counts, default=0)
    ax.set_xlim(0, max(peak * 1.18, 1))
    for i, v in enumerate(counts):
        if v:
            share = f" ({100.0 * v / total:.0f}%)" if total else ""
            ax.text(
                v + peak * 0.01,
                i,
                f"{v}{share}",
                va="center",
                fontsize=7,
                color=C_INK,
            )
    fig.tight_layout(pad=0.4)
    return fig
