"""
Shared building blocks for the polished HTML report exports.

The weekly staffing report set the visual standard (600px email-safe table,
navy title banner, KPI strip, section bars, zebra tables, base64-embedded
charts); the monthly and quarterly exports reuse these helpers so all
reports look like one family. Everything is inline-styled so the files
render identically in browsers, Outlook, and when pasted into email.
"""

from __future__ import annotations

import base64
import io

NAVY = "#052C47"
BLUE = "#2A4492"
RED = "#C12126"
GREEN = "#0F6E56"
LGRAY = "#E6E6E6"
MGRAY = "#CBC7D1"

EM = "—"  # em dash placeholder for empty cells


def fig_to_png_base64(fig) -> str:
    """Render a matplotlib figure to a base64 PNG (for <img src="data:...">)."""
    import matplotlib

    matplotlib.use("Agg")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def section_bar(title: str) -> str:
    return (
        f'<tr><td style="background:{NAVY};color:#ffffff;padding:8px 24px;'
        f'font-size:11px;font-weight:bold;letter-spacing:0.5px;">{title}</td></tr>'
    )


def body_cell(inner: str, padding: str = "12px 16px") -> str:
    return f'<tr><td style="padding:{padding};">{inner}</td></tr>'


def chart_img(b64: str, alt: str) -> str:
    return (
        f'<img src="data:image/png;base64,{b64}" alt="{alt}" '
        f'style="width:100%;max-width:568px;height:auto;display:block;" />'
    )


def data_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    right_cols: set[int] | None = None,
    total_row: bool = False,
) -> str:
    """Zebra-striped table matching the weekly report's look."""
    right_cols = right_cols or set()
    th = "".join(
        f'<th style="padding:6px 8px;text-align:{"right" if i in right_cols else "left"};">{h}</th>'
        for i, h in enumerate(headers)
    )
    body = ""
    for ri, row in enumerate(rows):
        is_total = total_row and ri == len(rows) - 1
        bg = MGRAY if is_total else (LGRAY if ri % 2 else "#ffffff")
        fw = "font-weight:bold;" if is_total else ""
        cells = ""
        for ci, cell in enumerate(row):
            align = "right" if ci in right_cols else ("left" if ci == 0 else "center")
            cells += (
                f'<td style="padding:6px 8px;text-align:{align};border:1px solid {MGRAY};{fw}">'
                f"{cell}</td>"
            )
        body += f'<tr style="background:{bg};">{cells}</tr>'
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="font-size:12px;border-collapse:collapse;">'
        f'<tr style="background:{NAVY};color:#ffffff;">{th}</tr>{body}</table>'
    )


def kpi_strip(kpis: list[tuple[str, str] | tuple[str, str, str]]) -> str:
    """Row of KPI cells: (label, value) or (label, value, sub_html).

    ``sub_html`` renders under the value — used for the vs-prior-period
    delta on board-level reports.
    """
    cells = ""
    for kpi in kpis:
        label, value = kpi[0], kpi[1]
        sub = kpi[2] if len(kpi) > 2 else ""
        sub_html = (
            f'<div style="font-size:10px;margin-top:2px;">{sub}</div>' if sub else ""
        )
        cells += (
            f'<td style="padding:8px 4px;text-align:center;border:1px solid {MGRAY};">'
            f'<div style="font-size:18px;font-weight:bold;color:{NAVY};">{value}</div>'
            f'<div style="font-size:11px;color:#333;">{label}</div>{sub_html}</td>'
        )
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        f"<tr>{cells}</tr></table>"
    )


def delta_html(current: float, prior: float, *, higher_is_better: bool = True) -> str:
    """Colored ▲/▼ change vs the prior period, in percentage points."""
    diff = round((current - prior) * 100, 1)
    if abs(diff) < 0.05:
        return '<span style="color:#666;">&#9654; 0.0 pts</span>'
    up = diff > 0
    good = up == higher_is_better
    arrow = "&#9650;" if up else "&#9660;"
    color = GREEN if good else RED
    return f'<span style="color:{color};font-weight:bold;">{arrow} {abs(diff):.1f} pts</span>'


def share_bar_rows(
    breakdown: list[tuple[str, int]], highlight: set[str]
) -> tuple[str, int]:
    """<tr> rows for an exception-mix table with inline share bars.

    Returns (rows_html, total). ``highlight`` codes get the red bar.
    """
    total = sum(c for _, c in breakdown)
    max_count = max((c for _, c in breakdown), default=1) or 1
    rows = ""
    for code, count in breakdown:
        pct = f"{100 * count / total:.0f}%" if total else EM
        color = RED if code in highlight else BLUE
        bar_w = int(100 * count / max_count) if count else 0
        rows += (
            f'<tr><td style="padding:6px 8px;font-weight:bold;border:1px solid {MGRAY};">{code}</td>'
            f'<td style="padding:6px 4px;text-align:right;border:1px solid {MGRAY};">{count}</td>'
            f'<td style="padding:6px 4px;text-align:right;border:1px solid {MGRAY};">{pct}</td>'
            f'<td style="padding:6px 8px;border:1px solid {MGRAY};">'
            f'<div style="background:{LGRAY};height:14px;border-radius:2px;">'
            f'<div style="background:{color};width:{bar_w}%;height:14px;"></div>'
            f"</div></td></tr>"
        )
    return rows, total


def exception_mix_table(breakdown: list[tuple[str, int]], highlight: set[str]) -> str:
    rows, total = share_bar_rows(breakdown, highlight)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="font-size:12px;border-collapse:collapse;">'
        f'<tr style="background:{LGRAY};">'
        f'<th align="left" style="padding:6px 8px;border:1px solid {MGRAY};">Type</th>'
        f'<th align="right" style="padding:6px 4px;border:1px solid {MGRAY};">Count</th>'
        f'<th align="right" style="padding:6px 4px;border:1px solid {MGRAY};">%</th>'
        f'<th align="left" style="padding:6px 8px;border:1px solid {MGRAY};">Share</th>'
        f"</tr>{rows}"
        f'<tr style="background:{MGRAY};font-weight:bold;">'
        f'<td style="padding:6px 8px;border:1px solid {MGRAY};">Total</td>'
        f'<td align="right" style="padding:6px 4px;border:1px solid {MGRAY};">{total}</td>'
        f'<td align="right" style="padding:6px 4px;border:1px solid {MGRAY};">{"100%" if total else EM}</td>'
        f'<td style="border:1px solid {MGRAY};"></td></tr></table>'
    )


def note(text: str) -> str:
    return f'<p style="font-size:11px;color:#555;margin:10px 0 0;">{text}</p>'


def report_shell(
    *,
    title: str,
    subtitle: str,
    meta: str,
    body: str,
    doc_title: str,
) -> str:
    """Complete HTML document: navy banner + sections + confidential footer."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{doc_title}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;">

<tr><td style="background:{NAVY};color:#ffffff;padding:20px 24px;">
<div style="font-size:20px;font-weight:bold;letter-spacing:0.5px;">{title}</div>
<div style="font-size:13px;color:{LGRAY};margin-top:6px;">{subtitle}</div>
<div style="font-size:11px;color:{MGRAY};margin-top:8px;">{meta}</div>
<div style="font-size:10px;color:#ffffff;margin-top:12px;font-weight:bold;">BOSTON MEDFLIGHT</div>
<div style="font-size:10px;color:{LGRAY};">CLINICAL OPERATIONS</div>
</td></tr>

{body}

<tr><td style="padding:16px 24px;font-size:11px;color:#666;border-top:1px solid {MGRAY};">
Boston MedFlight &middot; Clinical Operations &middot; Confidential
</td></tr>

</table></td></tr></table>
</body></html>"""
