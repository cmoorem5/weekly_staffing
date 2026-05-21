"""Shared validation rules for weekly staffing (CLI + Django)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .metrics import REQUIRED_TOTAL

if TYPE_CHECKING:
    from .models import KpiThreshold

# Fallbacks when kpi_thresholds row is missing (match DEFAULT_THRESHOLDS in db.py).
DEFAULT_STAFFING_ACTION_FLOOR = 0.90
DEFAULT_OT_ACTION_CEILING = 0.12
DEFAULT_SHIFT_EXCEPTION_MONITOR_CEILING = 0.25


def _threshold_float(value: float | None, default: float) -> float:
    return float(value) if value is not None else default


def staffing_action_floor(
    thresholds: dict[str, KpiThreshold] | None = None,
) -> float:
    """Staffing rate below this value requires notes (entering red zone)."""
    if thresholds:
        row = thresholds.get("Staffing Rate")
        if row is not None:
            return _threshold_float(row.yellow_min, DEFAULT_STAFFING_ACTION_FLOOR)
    return DEFAULT_STAFFING_ACTION_FLOOR


def ot_action_ceiling(thresholds: dict[str, KpiThreshold] | None = None) -> float:
    """OT dependency above this value requires notes (entering red zone)."""
    if thresholds:
        row = thresholds.get("OT Dependency")
        if row is not None:
            return _threshold_float(row.yellow_max, DEFAULT_OT_ACTION_CEILING)
    return DEFAULT_OT_ACTION_CEILING


def shift_exception_monitor_ceiling(
    thresholds: dict[str, KpiThreshold] | None = None,
) -> float:
    """Shift exception % above green band triggers monitor-level narrative."""
    if thresholds:
        row = thresholds.get("Shift Exception %")
        if row is not None:
            return _threshold_float(
                row.green_max, DEFAULT_SHIFT_EXCEPTION_MONITOR_CEILING
            )
    return DEFAULT_SHIFT_EXCEPTION_MONITOR_CEILING


def notes_required(
    staffing_rate: float,
    ot_dependency: float,
    filled_total: int,
    *,
    required_total: int = REQUIRED_TOTAL,
    base_staffed_gt_total: bool = False,
    thresholds: dict[str, KpiThreshold] | None = None,
) -> bool:
    """True when non-empty notes are required to justify the week's numbers."""
    sr_floor = staffing_action_floor(thresholds)
    ot_ceil = ot_action_ceiling(thresholds)
    if staffing_rate < sr_floor:
        return True
    if ot_dependency > ot_ceil:
        return True
    if filled_total > required_total + 10:
        return True
    if base_staffed_gt_total:
        return True
    return False


def notes_required_message(
    thresholds: dict[str, KpiThreshold] | None = None,
) -> str:
    """Human-readable rule summary for form validation errors."""
    sr_pct = staffing_action_floor(thresholds) * 100
    ot_pct = ot_action_ceiling(thresholds) * 100
    return (
        f"Notes are required when staffing rate < {sr_pct:g}%, "
        f"OT dependency > {ot_pct:g}%, any base staffed > total, "
        f"or filled > required+10."
    )
