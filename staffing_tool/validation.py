"""Shared validation rules for weekly staffing (CLI + Django)."""

from .metrics import REQUIRED_TOTAL


def notes_required(
    staffing_rate: float,
    ot_dependency: float,
    filled_total: int,
    *,
    required_total: int = REQUIRED_TOTAL,
    base_staffed_gt_total: bool = False,
) -> bool:
    """True when non-empty notes are required to justify the week's numbers."""
    if staffing_rate < 0.90:
        return True
    if ot_dependency > 0.12:
        return True
    if filled_total > required_total + 10:
        return True
    if base_staffed_gt_total:
        return True
    return False
