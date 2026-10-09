"""Plain-language summary lines for the top of leadership reports.

Pure functions over data the report loaders already hold, so the annual and
quarterly reports (PDF and HTML) say the same thing. Each line states a fact
and the target it is measured against; nothing here grades with color.
"""

from __future__ import annotations

import math

from staffing_tool import report_style as style


def _fmt(pct_value: float) -> str:
    return f"{pct_value:.1f}%"


def _met(value: float, target: float, higher_is_better: bool) -> bool:
    return value >= target if higher_is_better else value <= target


def summary_lines(
    *,
    unit: str,
    buckets: list[tuple[str, float, float, float]],
    avg_staffing: float,
    avg_ot: float,
    targets: dict[str, float],
    base_coverage: list[tuple[str, str, str, str, str]],
) -> list[str]:
    """Up to three summary lines: staffing vs target, OT peak, short bases.

    ``buckets`` are trend rows (label, staffing %, OT %, exception %) on a
    0-100 scale, one per ``unit`` ("month" or "week"); averages are
    fractions. A line whose target is not set says so rather than grading.
    """
    rows = [b for b in buckets if not math.isnan(b[1])]
    lines: list[str] = []

    staffing_target = targets.get("Staffing Rate")
    line = f"Staffing averaged {style.pct(avg_staffing)}"
    if staffing_target is not None and rows:
        met = sum(1 for b in rows if _met(b[1], 100 * staffing_target, True))
        line += (
            f" against a ≥ {100 * staffing_target:.0f}% target; "
            f"{met} of {len(rows)} {unit}s met it."
        )
    else:
        line += "."
    lines.append(line)

    if rows:
        peak = max(rows, key=lambda b: b[2])
        ot_target = targets.get("OT Dependency")
        target_txt = (
            f" (target ≤ {100 * ot_target:.0f}%)" if ot_target is not None else ""
        )
        when = f"the week of {peak[0]}" if unit == "week" else peak[0]
        lines.append(
            f"OT dependency averaged {style.pct(avg_ot)}{target_txt}, "
            f"highest in {when} at {_fmt(peak[2])}."
        )

    # (value, text) for every base/vehicle under its target, worst first; the
    # line names the three worst so it stays one line in a CEO summary.
    short: list[tuple[float, str]] = []
    for label, idx, metric in (
        ("RW", 2, "System RW Coverage %"),
        ("GR", 4, "System GR Coverage %"),
    ):
        target = targets.get(metric)
        if target is None:
            continue
        for row in base_coverage:
            value = style.pct_value(row[idx])
            if value is not None and value < 100 * target:
                short.append((value, f"{row[0]} {label} {_fmt(value)}"))
    if short:
        short.sort(key=lambda s: s[0])
        named = ", ".join(text for _v, text in short[:3])
        more = f", and {len(short) - 3} more" if len(short) > 3 else ""
        lines.append(f"Below the availability target: {named}{more}.")
    elif any(
        targets.get(m) is not None
        for m in ("System RW Coverage %", "System GR Coverage %")
    ):
        lines.append("Every base met its RW and GR availability targets.")
    return lines
