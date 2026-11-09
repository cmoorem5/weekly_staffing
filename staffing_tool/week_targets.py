"""Write the staffing-target figures a week's coverage percentages depend on.

Base coverage as imported counts staffed trucks per base. The CEO figures need
two things the per-base counts can't give:

- **Ground** is a system target of 3 day + 1 night truck a day, from any base:
  28 a week. The weekly figure is the total of every ground shift staffed (a
  fifth truck one day offsets a short day), capped at the plan, so only the plan
  is stored (``WeeklyStaffing.gr_target_planned``); the staffed total comes from
  the per-base counts, which the week edit page lets you correct.
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

from .metrics import compute_week_metrics, uses_current_definitions
from .models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
)
from .shortfall import (
    GROUND_DAY_SCOPE,
    GROUND_NIGHT_SCOPE,
    analyze_week,
    net_of_hand_corrections,
    planned_groups,
)


def _sync_filled(session: Session, week: WeeklyStaffing, coverages) -> None:
    """Store the vehicles-fully-staffed day/night counts for readers of the columns.

    ``compute_week_metrics`` derives them from the coverage on read, so the
    reports never depend on this; it keeps the stored columns (CLI, exports) in
    step. A week with no coverage rows keeps whatever it had.
    """
    if not coverages:
        return
    m = compute_week_metrics(week, list(coverages), session.query(BaseConfig).all())
    week.filled_day = m.filled_day
    week.filled_night = m.filled_night


def recompute_week_targets(session: Session, week_start: str) -> bool:
    """Refresh the derived columns for one week. True when derived from detail.

    Always re-syncs the stored filled counts from the coverage. When the week
    has name-level schedule rows it also refreshes the ground plan, the rotor wing
    plan removals, pilot vacancies (rotor wing days with no pilot) and overnights
    below coverage (nights with a planned night vehicle unstaffed), both net of
    days fixed by hand and of vehicles marked out of service.
    """
    if not uses_current_definitions(week_start):
        return False  # last FY stays exactly as it was reported
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
        week.gr_target_planned = None
        week.plan_removed_night = 0
        for c in coverages:
            c.rw_planned_removed = 0
        session.flush()
        _sync_filled(session, week, coverages)
        return False

    week.gr_target_planned = (
        result.planned[(GROUND_DAY_SCOPE, "GR")]
        + result.planned[(GROUND_NIGHT_SCOPE, "GR")]
    )
    week.plan_removed_night = result.planned_removed["N"]
    full_rw = {g.scope: g.slots * 7 for g in planned_groups() if g.service_type == "RW"}
    for c in coverages:
        full = full_rw.get(c.base_name)
        c.rw_planned_removed = (
            max(0, full - result.planned[(c.base_name, "RW")])
            if full is not None
            else 0
        )
    session.flush()
    net = net_of_hand_corrections(session, result, [week_start]) or result
    week.pilot_vacancies = sum(
        1 for s in net.shortfalls if s.service_type == "RW" and s.operator_missing
    )
    week.overnights_below = len({s.day for s in net.shortfalls if s.day_night == "N"})
    _sync_filled(session, week, coverages)
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
