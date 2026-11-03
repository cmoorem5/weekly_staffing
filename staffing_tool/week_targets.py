"""Write the staffing-target figures a week's coverage percentages depend on.

Base coverage as imported counts staffed trucks per base. The CEO figures need
two things the per-base counts can't give:

- **Ground** is a system target of 3 day + 1 night truck a day, from any base.
  A fifth truck on a day must not offset a gap on another day, so the system
  figure is capped per day and stored on ``WeeklyStaffing``
  (``gr_target_staffed_day`` / ``_night`` / ``gr_target_planned``).
- **No vehicle** (an aircraft or truck out of service, marked by hand) is not a
  staffing problem. A down aircraft with no crew comes off that base's plan
  (``WeeklyBaseCoverage.rw_planned_removed``) and a down truck lowers the ground
  plan only when too few trucks remain to fill it.

``recompute_week_targets`` derives both from the stored OPS View assignments and
the outage marks. It is safe to rerun: call it after an import and whenever a
no-vehicle mark changes. A week with no name-level rows is left on the legacy
per-base numbers (the columns are cleared to NULL), never guessed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import WeeklyBaseCoverage, WeeklyOpsViewAssignment, WeeklyStaffing
from .shortfall import (
    GROUND_DAY_SCOPE,
    GROUND_NIGHT_SCOPE,
    analyze_week,
    planned_groups,
)


def recompute_week_targets(session: Session, week_start: str) -> bool:
    """Refresh the target columns for one week. True when derived from detail."""
    week = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    if week is None:
        return False
    coverages = (
        session.query(WeeklyBaseCoverage)
        .filter(WeeklyBaseCoverage.week_start == week_start)
        .all()
    )
    result = analyze_week(session, week_start)
    if result is None:
        week.gr_target_staffed_day = None
        week.gr_target_staffed_night = None
        week.gr_target_planned = None
        for c in coverages:
            c.rw_planned_removed = 0
        return False

    week.gr_target_staffed_day = result.staffed[(GROUND_DAY_SCOPE, "GR")]
    week.gr_target_staffed_night = result.staffed[(GROUND_NIGHT_SCOPE, "GR")]
    week.gr_target_planned = (
        result.planned[(GROUND_DAY_SCOPE, "GR")]
        + result.planned[(GROUND_NIGHT_SCOPE, "GR")]
    )
    full_rw = {g.scope: g.slots * 7 for g in planned_groups() if g.service_type == "RW"}
    for c in coverages:
        full = full_rw.get(c.base_name)
        c.rw_planned_removed = (
            max(0, full - result.planned[(c.base_name, "RW")])
            if full is not None
            else 0
        )
    return True


def recompute_all_weeks(session: Session) -> dict[str, bool]:
    """Recompute every stored week that has name-level OPS View rows."""
    weeks = [
        w
        for (w,) in session.query(WeeklyOpsViewAssignment.week_start)
        .group_by(WeeklyOpsViewAssignment.week_start)
        .order_by(WeeklyOpsViewAssignment.week_start)
        .all()
    ]
    return {w: recompute_week_targets(session, w) for w in weeks}
