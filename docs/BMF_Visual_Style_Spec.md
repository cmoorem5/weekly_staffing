# BMF Clinical Operations Reports — Shared Visual Style Specification

**Applies to:** Weekly Staffing Report, Quarterly Staffing Report, and all derived PDF reports built with Python + ReportLab + matplotlib.

---

## 1. Purpose

This spec is the single source of truth for visual style across all BMF operational report builds. Any new report or future chat building against this codebase should read this file before writing a line of layout code. Deviating from this spec requires explicit sign-off in the build script comments.

---

## 2. Brand Colors

```python
NAVY  = colors.HexColor('#052C47')   # Title banner bg, section bar bg, table header rows
BLUE  = colors.HexColor('#2A4492')   # Primary chart series, accent elements
LGRAY = colors.HexColor('#E6E6E6')   # Alternating row fill, sub-header fill
MGRAY = colors.HexColor('#CBC7D1')   # Table borders, total-row fill, dividers
RED   = colors.HexColor('#C12126')   # Alerts, top exception drivers, OT trend line
WHITE = colors.white
BLACK = colors.black
```

Hex values are canonical. Do not substitute.

---

## 3. Typography

### Fonts

| Use | Font | ReportLab Registered Name | Fallback |
|-----|------|--------------------------|----------|
| Body text | Barlow Regular | `BarlowRegular` | `Helvetica` |
| Body bold | Barlow Bold | `BarlowBold` | `Helvetica-Bold` |
| Semibold labels | Barlow SemiBold | `BarlowSemiBold` | `Helvetica` |
| Section/banner labels | Barlow Condensed Bold | `BarlowCondensedBold` | `Helvetica-Bold` |
| Table numeric values | IBM Plex Mono Regular | `IBMPlexMonoRegular` | `Courier` |
| Table numeric bold | IBM Plex Mono Bold | `IBMPlexMonoBold` | `Courier-Bold` |

Font files live in `./fonts/` relative to the build script. Registration must happen at module load, before any flowable or canvas draw call.

```python
FONT_DIR = os.path.join(os.path.dirname(__file__), 'fonts')

def register_fonts():
    pdfmetrics.registerFont(TTFont('BarlowRegular',       os.path.join(FONT_DIR, 'Barlow-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('BarlowBold',          os.path.join(FONT_DIR, 'Barlow-Bold.ttf')))
    pdfmetrics.registerFont(TTFont('BarlowSemiBold',      os.path.join(FONT_DIR, 'Barlow-SemiBold.ttf')))
    pdfmetrics.registerFont(TTFont('BarlowCondensedBold', os.path.join(FONT_DIR, 'BarlowCondensed-Bold.ttf')))
    pdfmetrics.registerFont(TTFont('IBMPlexMonoRegular',  os.path.join(FONT_DIR, 'IBMPlexMono-Regular.ttf')))
    pdfmetrics.registerFont(TTFont('IBMPlexMonoBold',     os.path.join(FONT_DIR, 'IBMPlexMono-Bold.ttf')))
```

### Type Scale

| Element | Font | Size (pt) |
|---------|------|-----------|
| Report title (banner) | BarlowCondensedBold | 18 |
| Report subtitle / date range | BarlowRegular | 10 |
| Section bar label | BarlowCondensedBold | 9 |
| KPI value | BarlowBold | 20 |
| KPI label | BarlowRegular | 8 |
| Table header | BarlowBold | 8 |
| Table body | BarlowRegular | 8 |
| Table numeric value | IBMPlexMonoRegular | 8 |
| Table total row | IBMPlexMonoBold | 8 |
| Chart axis labels | BarlowRegular | 7 |
| Chart title (if used) | BarlowSemiBold | 9 |
| Footer | BarlowRegular | 7 |

---

## 4. Page Layout

```python
PAGE_SIZE   = letter          # 8.5 × 11 in
MARGIN      = 0.5 * inch      # all four sides
USABLE_W    = 7.5 * inch      # 8.5 − 2×0.5
USABLE_H    = 10.0 * inch     # 11 − 2×0.5
```

Use `SimpleDocTemplate` with `leftMargin=rightMargin=topMargin=bottomMargin=0.5*inch`.

### Column Grids

| Use | Column count | Each column |
|-----|-------------|-------------|
| KPI cards | 6 | 1.25 in |
| Two-column table side-by-side | **Forbidden for tall content** | see §7 |
| Half-width sub-tables (header only) | 2 | 3.75 in each |
| Full-width table | 1 | 7.5 in |

---

## 5. Table Style Rules

All tables use `TableStyle`. The following is the baseline style. Apply it first, then add overrides.

```python
BASE_TABLE_STYLE = [
    ('BACKGROUND',  (0, 0), (-1, 0),  NAVY),          # header row
    ('TEXTCOLOR',   (0, 0), (-1, 0),  WHITE),
    ('FONTNAME',    (0, 0), (-1, 0),  'BarlowBold'),
    ('FONTSIZE',    (0, 0), (-1, 0),  8),
    ('FONTNAME',    (0, 1), (-1, -1), 'BarlowRegular'),
    ('FONTSIZE',    (0, 1), (-1, -1), 8),
    ('ROWBACKGROUNDS', (0, 1), (-1, -1), [WHITE, LGRAY]),  # alternating rows
    ('GRID',        (0, 0), (-1, -1), 0.5, MGRAY),
    ('TOPPADDING',  (0, 0), (-1, -1), 4),
    ('BOTTOMPADDING',(0,0), (-1, -1), 4),
    ('LEFTPADDING', (0, 0), (-1, -1), 6),
    ('RIGHTPADDING',(0, 0), (-1, -1), 6),
    ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
]
```

**Numeric columns:** apply `IBMPlexMonoRegular` and `ALIGN RIGHT` to every data cell in a numeric column. Header cell for that column stays `BarlowBold` and `ALIGN CENTER`.

**Total rows:** background `MGRAY`, font `IBMPlexMonoBold`, applied after alternating row rule.

**Sub-header rows** (grouping labels within a table): background `LGRAY`, font `BarlowSemiBold`.

---

## 6. Chart Style Rules (matplotlib)

All charts are generated as `BytesIO` PNG buffers at 150 dpi, then embedded via `reportlab.platypus.Image`. Do not write chart files to disk.

```python
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from io import BytesIO

# Color constants (match ReportLab palette exactly)
C_NAVY  = '#052C47'
C_BLUE  = '#2A4492'
C_LGRAY = '#E6E6E6'
C_MGRAY = '#CBC7D1'
C_RED   = '#C12126'

def _base_figure(w_in, h_in):
    """Return a (fig, ax) with BMF base styling applied."""
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
    plt.rcParams.update({'font.family': 'sans-serif', 'font.sans-serif': ['Arial']})
    return fig, ax

def chart_to_image(fig, width_in_doc, height_in_doc):
    """Save fig to BytesIO and return a ReportLab Image flowable."""
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=width_in_doc, height=height_in_doc)
```

### Chart Color Assignments

| Series | Color |
|--------|-------|
| Primary metric (line or bar) | `C_BLUE` |
| Secondary metric (line) | `C_NAVY` |
| Alert / OT / exception highlight | `C_RED` |
| Bar baseline / background element | `C_LGRAY` |
| Grid lines | `C_MGRAY`, linewidth 0.5, linestyle `--` |

### Dual-Scale Charts

When a chart carries two y-axes (e.g., staffing rate on left, OT % on right), the secondary axis must be labeled explicitly and the legend must identify which scale each series uses.

---

## 7. Pagination Rules

These rules exist to prevent the white-gap and split-table problems encountered in prior builds. Follow them without exception.

### Rule 1: Stacked full-width tables, not side-by-side

**Never place two tall tables side-by-side in a two-column layout.** Side-by-side layouts implemented as nested tables or `HRFlowable` splits will break across page boundaries unpredictably and produce white gaps.

For content that appears logically paired (e.g., "Coverage RW" and "Coverage GR"), stack them vertically as two full-width (7.5 in) tables with a small `Spacer(1, 8)` between them.

Exception: side-by-side is acceptable for **header-only or single-row** summary blocks where total row count does not exceed 3.

### Rule 2: KeepTogether only for short blocks

`KeepTogether` prevents a flowable group from splitting across a page break by buffering the entire group before placing it. If the group is taller than half the usable page height (~5 in), ReportLab cannot honor the request and will place the block anyway, sometimes producing a gap.

**Hard limit:** Do not wrap `KeepTogether` around any block taller than 4.5 in (roughly 15 data rows + a section bar). For longer sections, accept the page break and use a `section_bar` repeat at the top of the continuation if needed.

### Rule 3: Section bars are plain flowables

Section bars (navy full-width label rows) must be `Table` flowables appended directly to `story`, not wrapped in a frame or nested table. This ensures they reflow correctly with the content below them.

### Rule 4: Spacers between sections

Use `Spacer(1, 10)` between every logical section. Do not use `Spacer(1, 0)` as a layout trick; it causes ReportLab to place unnecessary gaps in some frame states.

---

## 8. Section Bar Helper

```python
def section_bar(text):
    t = Table([[text]], colWidths=[USABLE_W])
    t.setStyle(TableStyle([
        ('BACKGROUND',   (0,0), (-1,-1), NAVY),
        ('TEXTCOLOR',    (0,0), (-1,-1), WHITE),
        ('FONTNAME',     (0,0), (-1,-1), 'BarlowCondensedBold'),
        ('FONTSIZE',     (0,0), (-1,-1), 9),
        ('TOPPADDING',   (0,0), (-1,-1), 5),
        ('BOTTOMPADDING',(0,0), (-1,-1), 5),
        ('LEFTPADDING',  (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    return t
```

---

## 9. Title Banner Helper

```python
def title_banner(title_text, subtitle_text, meta_line=None):
    """Navy cover block with optional prepared/confidential line and BMF branding."""
    rows = [[title_text], [subtitle_text]]
    if meta_line:
        rows.append([meta_line])
    rows.extend([['BOSTON MEDFLIGHT'], ['CLINICAL OPERATIONS']])
    # ... TableStyle per staffing_tool/report_style.py
```

---

## 10. KPI Card Row Helper

```python
def kpi_row(kpi_list):
    """kpi_list: list of (label_str, value_str) tuples. Max 6."""
    n = len(kpi_list)
    col_w = USABLE_W / n
    values = [[v for _, v in kpi_list]]
    labels = [[l for l, _ in kpi_list]]
    t = Table(values + labels, colWidths=[col_w] * n, rowHeights=[0.55*inch, 0.3*inch])
    style = [
        ('BACKGROUND',  (0,0), (-1,-1), WHITE),
        ('BOX',         (0,0), (-1,-1), 0.5, MGRAY),
        ('INNERGRID',   (0,0), (-1,-1), 0.5, MGRAY),
        # value row
        ('FONTNAME',    (0,0), (-1,0),  'IBMPlexMonoBold'),
        ('FONTSIZE',    (0,0), (-1,0),  20),
        ('TEXTCOLOR',   (0,0), (-1,0),  NAVY),
        ('ALIGN',       (0,0), (-1,0),  'CENTER'),
        ('VALIGN',      (0,0), (-1,0),  'BOTTOM'),
        # label row
        ('FONTNAME',    (0,1), (-1,1),  'BarlowRegular'),
        ('FONTSIZE',    (0,1), (-1,1),  8),
        ('TEXTCOLOR',   (0,1), (-1,1),  BLACK),
        ('ALIGN',       (0,1), (-1,1),  'CENTER'),
        ('VALIGN',      (0,1), (-1,1),  'TOP'),
        ('TOPPADDING',  (0,0), (-1,-1), 4),
        ('BOTTOMPADDING',(0,0),(-1,-1), 4),
    ]
    t.setStyle(TableStyle(style))
    return t
```

---

## 11. Footer and Running Header

Polish reference: `output/BMF FY27 Clinical Ops Expansion.pdf`

All reports use `staffing_tool.report_style.make_page_callbacks()` and `NumberedCanvas` for consistent page chrome.

**Page 1:** Navy title banner with BOSTON MEDFLIGHT / CLINICAL OPERATIONS branding, prepared date, and CONFIDENTIAL line. Footer only (no running header).

**Pages 2+:** Running header — `BOSTON MEDFLIGHT · {REPORT TITLE} · CONFIDENTIAL` in Barlow Condensed Bold 7pt, navy, with MGRAY rule below.

**All pages footer:** `Boston MedFlight · {short title} · Confidential · Page X of Y` centered at 0.35 in, Barlow Regular 7pt, MGRAY.

Implementation lives in `staffing_tool/report_style.py` — import `make_page_callbacks`, `NumberedCanvas`, and pass to `doc.build(..., canvasmaker=NumberedCanvas)`.

---

## 12. File Naming and Output Location

Reports write to `./output/` (created automatically).

| Report | Output filename |
|--------|----------------|
| Weekly staffing | `output/BMF_Weekly_Staffing_YYYY-MM-DD.pdf` |
| Quarterly staffing | `output/BMF_Quarterly_Staffing_FY{YY}Q{N}.pdf` |

---

## 13. Shared Module

Import layout helpers from `staffing_tool.report_style` rather than duplicating in each build script:

```python
from staffing_tool.report_style import (
    register_fonts, ensure_output_dir, OUTPUT_DIR,
    section_bar, title_banner, kpi_row, num_style_cells,
    base_figure, chart_to_image,
    NumberedCanvas, make_page_callbacks, F,
)
```

Font files belong in `./fonts/`. Run `python scripts/setup_report_fonts.py` once to download them. When fonts are missing, `F()` falls back to Helvetica/Courier automatically.

---

## 14. Dependencies

```
reportlab>=4.0
matplotlib>=3.7
```

No other charting or PDF library. Do not mix ReportLab canvas charts and matplotlib charts in the same document. Use matplotlib exclusively for all chart rendering.

---

## 15. Gotchas and Known Failure Modes

- **KeepTogether + tall blocks:** If a `KeepTogether` block is taller than the available page space, ReportLab silently ignores the keep and may produce a blank gap before the block. Always check rendered height before applying `KeepTogether`. See Rule 2 in §7.
- **Side-by-side table splits:** Two-column table layouts using nested `Table` cells will split at page boundaries regardless of `KeepTogether`. See Rule 1 in §7.
- **Font not registered:** All `pdfmetrics.registerFont()` calls must happen before any `Paragraph` or `TableStyle` that references that font name. Call `register_fonts()` at the top of `main()`.
- **matplotlib backend:** Always set `matplotlib.use('Agg')` before importing `pyplot`. If omitted, the script will fail in headless environments.
- **IBM Plex Mono for numerics:** Apply to data cells only, not header cells. Header cells use `BarlowBold`.
- **Em dash:** Use the literal Unicode character `\u2014` (`—`) for missing/N/A values in tables. Do not use `--` or `&mdash;`.
- **Quarterly refresh checklist:** Update `PERIOD`, `DATES`, `WEEKS`, all data constants, and the output filename. Do a project-wide find for any literal quarter string (e.g., `FY26 Q2`) before running.
- **Weekly refresh checklist:** Update `WEEK_OF`, `WEEK_DATES`, and all data rows. The script should have a single `DATA` block at the top; nothing else should need touching.
