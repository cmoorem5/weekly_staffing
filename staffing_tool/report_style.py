"""
Shared visual style for BMF Clinical Operations PDF reports.
Canonical reference: docs/BMF_Visual_Style_Spec.md
Polish reference: output/BMF FY27 Clinical Ops Expansion.pdf
"""

import os

import matplotlib

from staffing_tool.paths import FONT_DIR as _FONT_DIR
from staffing_tool.paths import OUTPUT_DIR as _OUTPUT_DIR

matplotlib.use('Agg')
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
NAVY = colors.HexColor('#052C47')
BLUE = colors.HexColor('#2A4492')
LGRAY = colors.HexColor('#E6E6E6')
MGRAY = colors.HexColor('#CBC7D1')
RED = colors.HexColor('#C12126')
WHITE = colors.white
BLACK = colors.black

C_NAVY = '#052C47'
C_BLUE = '#2A4492'
C_LGRAY = '#E6E6E6'
C_MGRAY = '#CBC7D1'
C_RED = '#C12126'

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
        ('BarlowRegular', 'Barlow-Regular.ttf', 'Helvetica'),
        ('BarlowBold', 'Barlow-Bold.ttf', 'Helvetica-Bold'),
        ('BarlowSemiBold', 'Barlow-SemiBold.ttf', 'Helvetica'),
        ('BarlowCondensedBold', 'BarlowCondensed-Bold.ttf', 'Helvetica-Bold'),
        ('IBMPlexMonoRegular', 'IBMPlexMono-Regular.ttf', 'Courier'),
        ('IBMPlexMonoBold', 'IBMPlexMono-Bold.ttf', 'Courier-Bold'),
    ]
    for reg_name, filename, fallback in specs:
        path = os.path.join(FONT_DIR, filename)
        if os.path.isfile(path):
            if reg_name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(reg_name, path))
            _FONT_MAP[reg_name] = reg_name
        else:
            _FONT_MAP[reg_name] = fallback

    barlow_regular = os.path.join(FONT_DIR, 'Barlow-Regular.ttf')
    if os.path.isfile(barlow_regular):
        for fname in ('Barlow-Regular.ttf', 'Barlow-Bold.ttf'):
            p = os.path.join(FONT_DIR, fname)
            if os.path.isfile(p):
                fm.fontManager.addfont(p)
        plt.rcParams.update({
            'font.family': 'Barlow',
            'font.sans-serif': ['Barlow', 'Arial', 'Helvetica'],
        })
    else:
        plt.rcParams.update({
            'font.family': 'sans-serif',
            'font.sans-serif': ['Arial', 'Helvetica'],
        })

    _FONTS_REGISTERED = True


def F(name):
    """Resolve registered font name with fallback."""
    return _FONT_MAP.get(name, name)


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# LAYOUT COMPONENTS
# ---------------------------------------------------------------------------

def section_bar(text):
    t = Table([[text]], colWidths=[USABLE_W])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, -1), WHITE),
        ('FONTNAME', (0, 0), (-1, -1), F('BarlowCondensedBold')),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    return t


def title_banner(title_text, subtitle_text, meta_line=None):
    """Navy cover banner with BMF branding — matches Expansion report polish."""
    rows = [[title_text], [subtitle_text]]
    if meta_line:
        rows.append([meta_line])
    rows.extend([['BOSTON MEDFLIGHT'], ['CLINICAL OPERATIONS']])

    brand_start = len(rows) - 2
    t = Table(rows, colWidths=[USABLE_W])
    style = [
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        # title
        ('TEXTCOLOR', (0, 0), (0, 0), WHITE),
        ('FONTNAME', (0, 0), (0, 0), F('BarlowCondensedBold')),
        ('FONTSIZE', (0, 0), (0, 0), 18),
        ('TOPPADDING', (0, 0), (0, 0), 14),
        ('BOTTOMPADDING', (0, 0), (0, 0), 4),
        # subtitle
        ('TEXTCOLOR', (0, 1), (0, 1), LGRAY),
        ('FONTNAME', (0, 1), (0, 1), F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (0, 1), 10),
        ('TOPPADDING', (0, 1), (0, 1), 0),
        ('BOTTOMPADDING', (0, 1), (0, 1), 6 if meta_line else 10),
    ]
    if meta_line:
        style += [
            ('TEXTCOLOR', (0, 2), (0, 2), LGRAY),
            ('FONTNAME', (0, 2), (0, 2), F('BarlowRegular')),
            ('FONTSIZE', (0, 2), (0, 2), 8),
            ('TOPPADDING', (0, 2), (0, 2), 0),
            ('BOTTOMPADDING', (0, 2), (0, 2), 10),
        ]
    style += [
        ('TEXTCOLOR', (0, brand_start), (0, brand_start), WHITE),
        ('FONTNAME', (0, brand_start), (0, brand_start), F('BarlowCondensedBold')),
        ('FONTSIZE', (0, brand_start), (0, brand_start), 8),
        ('TOPPADDING', (0, brand_start), (0, brand_start), 4),
        ('BOTTOMPADDING', (0, brand_start), (0, brand_start), 0),
        ('TEXTCOLOR', (0, brand_start + 1), (0, brand_start + 1), LGRAY),
        ('FONTNAME', (0, brand_start + 1), (0, brand_start + 1), F('BarlowCondensedBold')),
        ('FONTSIZE', (0, brand_start + 1), (0, brand_start + 1), 8),
        ('TOPPADDING', (0, brand_start + 1), (0, brand_start + 1), 0),
        ('BOTTOMPADDING', (0, brand_start + 1), (0, brand_start + 1), 14),
    ]
    t.setStyle(TableStyle(style))
    return t


def kpi_row(kpi_list):
    n = len(kpi_list)
    col_w = USABLE_W / n
    values = [[v for _, v in kpi_list]]
    labels = [[label for label, _ in kpi_list]]
    t = Table(values + labels, colWidths=[col_w] * n,
              rowHeights=[0.55 * inch, 0.3 * inch])
    # Vertical dividers only — INNERGRID also draws a horizontal rule that cuts through values.
    style = [
        ('BACKGROUND', (0, 0), (-1, -1), WHITE),
        ('BOX', (0, 0), (-1, -1), 0.5, MGRAY),
        ('FONTNAME', (0, 0), (-1, 0), F('IBMPlexMonoBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 20),
        ('TEXTCOLOR', (0, 0), (-1, 0), NAVY),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
        ('FONTNAME', (0, 1), (-1, 1), F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, 1), 8),
        ('TEXTCOLOR', (0, 1), (-1, 1), BLACK),
        ('ALIGN', (0, 1), (-1, 1), 'CENTER'),
        ('VALIGN', (0, 1), (-1, 1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 2),
        ('TOPPADDING', (0, 1), (-1, 1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 6),
    ]
    for col in range(1, n):
        style.append(('LINEBEFORE', (col, 0), (col, -1), 0.5, MGRAY))
    t.setStyle(TableStyle(style))
    return t


def num_style_cells(col_indices, start_row=1, end_row=-1):
    return (
        [('FONTNAME', (col, start_row), (col, end_row), F('IBMPlexMonoRegular'))
         for col in col_indices] +
        [('ALIGN', (col, start_row), (col, end_row), 'RIGHT') for col in col_indices]
    )


def base_table_style(total_row=None):
    """Return opening TableStyle commands shared by all data tables."""
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    if total_row is not None:
        style += [
            ('ROWBACKGROUNDS', (0, 1), (-1, total_row - 1), [WHITE, LGRAY]),
            ('BACKGROUND', (0, total_row), (-1, total_row), MGRAY),
            ('FONTNAME', (0, total_row), (-1, total_row), F('IBMPlexMonoBold')),
        ]
    else:
        style.append(('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LGRAY]))
    return style


# ---------------------------------------------------------------------------
# CHART HELPERS
# ---------------------------------------------------------------------------

def base_figure(w_in, h_in):
    fig, ax = plt.subplots(figsize=(w_in, h_in))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['left'].set_color(C_MGRAY)
    ax.spines['bottom'].set_color(C_MGRAY)
    ax.tick_params(colors='#333333', labelsize=7)
    ax.xaxis.label.set_fontsize(7)
    ax.yaxis.label.set_fontsize(7)
    return fig, ax


def full_width_col_widths(relative: list[float]) -> list[float]:
    """Scale column weight ratios to span USABLE_W (match section_bar width)."""
    total = sum(relative)
    return [USABLE_W * w / total for w in relative]


def chart_to_image(fig, width_in_doc, height_in_doc=None):
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
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
        loc='upper center',
        bbox_to_anchor=(0.5, 0.04),
        ncol=ncol,
        fontsize=6,
        framealpha=0.95,
        edgecolor=C_MGRAY,
        facecolor='white',
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
        if hasattr(self, '_page_number_draw'):
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
        c.setFont(F('BarlowRegular'), 7)
        c.setFillColor(MGRAY)
        text = _footer_text(c.getPageNumber(), page_count)
        c.drawCentredString(PAGE_SIZE[0] / 2, 0.35 * inch, text)
        c.restoreState()

    def _draw_running_header(c):
        c.saveState()
        w, h = PAGE_SIZE
        c.setFont(F('BarlowCondensedBold'), 7)
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
