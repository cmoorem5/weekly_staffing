"""
RAG (Red/Amber/Green) threshold evaluation and direction arrows.
Supports both "higher is better" and "lower is better" metrics.
"""

from typing import Literal

from .models import KpiThreshold

RAG = Literal["Green", "Yellow", "Red"]
DIRECTION = Literal["↑", "↓", "→"]


def _in_range(value: float, lo: float | None, hi: float | None) -> bool:
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def evaluate_rag(value: float, threshold: KpiThreshold) -> RAG:
    """
    Evaluate RAG status using explicit ranges. Check green first, then yellow, then red.
    Ranges are inclusive [min, max]. E.g. green = [green_min, green_max].
    """
    g_min, g_max = threshold.green_min, threshold.green_max
    y_min, y_max = threshold.yellow_min, threshold.yellow_max
    r_min, r_max = threshold.red_min, threshold.red_max

    if _in_range(value, g_min, g_max):
        return "Green"
    if _in_range(value, y_min, y_max):
        return "Yellow"
    if _in_range(value, r_min, r_max):
        return "Red"
    # Outside all ranges: treat as red if above green, else by higher_is_better
    higher = (threshold.higher_is_better or 0) != 0
    if higher and g_min is not None and value >= g_min:
        return "Green"
    if higher and y_min is not None and value >= y_min:
        return "Yellow"
    if not higher and g_max is not None and value <= g_max:
        return "Green"
    if not higher and y_max is not None and value <= y_max:
        return "Yellow"
    return "Red"


def compare_direction(current: float, prior: float | None) -> DIRECTION:
    """Return ↑ (improvement), ↓ (worsening), or → (no change / no prior)."""
    if prior is None:
        return "→"
    if current > prior:
        return "↑"
    if current < prior:
        return "↓"
    return "→"


# Metrics where a decrease is improvement; arrow flipped: ↑ = improvement, ↓ = worsening.
LOWER_IS_BETTER_METRICS = frozenset(
    {
        "OT Dependency",
        "Leave Exposure",
        "Overnights Below Coverage",
        "Pilot Vacancies",
    }
)


def direction_for_metric(
    metric_name: str, current: float, prior: float | None
) -> DIRECTION:
    """
    Direction arrow for display. Raw: ↑ = current > prior, ↓ = current < prior, → = same.
    For "lower is better" metrics we flip so ↑ = improvement, ↓ = worsening.
    """
    raw = compare_direction(current, prior)
    if metric_name not in LOWER_IS_BETTER_METRICS:
        return raw
    if raw == "↑":
        return "↓"
    if raw == "↓":
        return "↑"
    return "→"
