"""
Computed staffing KPIs from weekly_staffing and weekly_base_coverage.

Staffing model constants (per spec):
- Required crew shifts: day = 56, night = 28, total = 84.
- Total person-shifts per week: 217 (RN 84 + Medic 84 + EMT 49).

Ground (GR) coverage:
- Per-base GR % uses each base's gr_total_unit_days from BaseConfig (planning slots).
- System GR % uses SYSTEM_GR_MAX_SHIFTS_PER_WEEK: total staffed GR unit-days (all
  bases) divided by this operational max (28), not the sum of per-base caps
  (which can exceed what you can actually staff system-wide).
"""

from dataclasses import dataclass
from typing import Any, cast

from .models import BaseConfig, WeeklyBaseCoverage, WeeklyPersonShift, WeeklyStaffing

REQUIRED_DAY = 56
REQUIRED_NIGHT = 28
REQUIRED_TOTAL = REQUIRED_DAY + REQUIRED_NIGHT  # 84

# RN 84 + Medic 84 + EMT 49
TOTAL_PERSON_SHIFTS = 217

# Person-shift capacity per role per week (matches WeeklyPersonShift.role).
ROLE_CAPACITY_PER_WEEK = {"RN": 84, "MEDIC": 84, "EMT": 49}
ROLE_FILL_LABELS = {"RN": "RN (Flight Nurse)", "MEDIC": "Paramedic", "EMT": "EMT"}

# Opportunistic extra Bedford ambulance unit codes (staffed when available,
# not counted toward minimum) — excluded from role-fill "worked" counts so
# they don't inflate fill rate past the required-line capacity above.
EXTRA_UNIT_CODES = {"GR2", "NG2"}

# Max GR unit-days (D+N) staffable system-wide per week — denominator for
# System GR % only.
SYSTEM_GR_MAX_SHIFTS_PER_WEEK = 28


@dataclass
class PeriodRollups:
    """Weekly averages vs pooled period rates (sum numerators / sum denominators)."""

    n_weeks: int
    filled_total: int
    ot_shifts: int
    leave_total: int
    avg_staffing_rate: float
    avg_ot_dependency: float
    avg_leave_exposure: float
    avg_system_rw_pct: float
    avg_system_gr_pct: float
    pooled_staffing_rate: float
    pooled_ot_dependency: float
    pooled_leave_exposure: float
    pooled_system_rw_pct: float
    pooled_system_gr_pct: float
    # Day/night split of the staffing rate (added later; defaults keep
    # any positional construction working).
    avg_day_staffing_rate: float = 0.0
    avg_night_staffing_rate: float = 0.0


@dataclass
class WeekMetrics:
    """All computed metrics for one week."""

    week_start: str
    required_day: int
    required_night: int
    required_total: int
    filled_day: int
    filled_night: int
    filled_total: int
    vacancies: int
    staffing_rate: float
    ot_shifts: int  # total OT shifts across roles
    ot_dependency: float
    leave_total: int
    leave_exposure: float
    overnights_below: int
    pilot_vacancies: int
    # Base coverage (system rollup)
    rw_total_unit_days: int  # sum across bases from base_config
    gr_total_unit_days: int
    rw_staffed_unit_days: int
    gr_staffed_unit_days: int
    system_rw_pct: float
    system_gr_pct: float
    # Per-base (optional, for detail)
    base_metrics: dict[str, dict[str, float]] | None = (
        None  # base_name -> {rw_pct, gr_pct}
    )
    # Day/night split of the staffing rate.
    day_staffing_rate: float = 0.0
    night_staffing_rate: float = 0.0


def compute_week_metrics(
    row: WeeklyStaffing,
    base_coverages: list[WeeklyBaseCoverage],
    base_configs: list[BaseConfig],
) -> WeekMetrics:
    """Compute all KPIs for a single week."""
    # ORM instance: class-level Column types confuse static analysis
    ws = cast(Any, row)

    # Required shifts are fixed (8 day crews + 4 night crews, 7 days).
    required_day = REQUIRED_DAY
    required_night = REQUIRED_NIGHT
    required_total = REQUIRED_TOTAL
    filled_day = int(ws.filled_day)
    filled_night = int(ws.filled_night)
    filled_total = filled_day + filled_night
    vacancies = max(0, required_total - filled_total)
    staffing_rate = (
        float(filled_total) / float(required_total) if required_total else 0.0
    )

    # OT dependency: separated RN/Medic/EMT with day/night split when present.
    ot_rn_day = int(ws.ot_rn_day or 0)
    ot_rn_night = int(ws.ot_rn_night or 0)
    ot_medic_day = int(ws.ot_medic_day or 0)
    ot_medic_night = int(ws.ot_medic_night or 0)
    ot_emt_day = int(ws.ot_emt_day or 0)
    ot_emt_night = int(ws.ot_emt_night or 0)
    total_ot = (
        ot_rn_day
        + ot_rn_night
        + ot_medic_day
        + ot_medic_night
        + ot_emt_day
        + ot_emt_night
    )
    if total_ot == 0:
        total_ot = int(ws.ot_rn or 0) + int(ws.ot_medic or 0) + int(ws.ot_emt or 0)
    if total_ot == 0:
        total_ot = int(ws.ot_shifts or 0)
    ot_dependency = float(total_ot) / float(filled_total) if filled_total else 0.0

    # PFML rolled into LOA for exposure; leave_total includes JURY and BREV.
    leave_total = (
        int(ws.leave_at)
        + int(ws.leave_lt)
        + int(ws.leave_sick)
        + int(ws.leave_loa)
        + int(ws.leave_pfml or 0)
        + int(ws.leave_jury or 0)
        + int(ws.leave_brev or 0)
    )
    leave_exposure = (
        float(leave_total) / float(TOTAL_PERSON_SHIFTS) if TOTAL_PERSON_SHIFTS else 0.0
    )

    base_by_name = {b.base_name: b for b in base_configs}
    rw_total_unit_days = sum(int(b.rw_total_unit_days) for b in base_configs)
    gr_total_unit_days = sum(int(b.gr_total_unit_days) for b in base_configs)

    base_metrics: dict[str, dict[str, float]] = {}
    rw_staffed = 0
    gr_staffed = 0

    for c in base_coverages:
        bc = cast(Any, c)
        cfg = base_by_name.get(bc.base_name)
        if not cfg:
            continue
        rw_d = int(bc.rw_staffed_day or 0)
        rw_n = int(bc.rw_staffed_night or 0)
        gr_d = int(bc.gr_staffed_day or 0)
        gr_n = int(bc.gr_staffed_night or 0)
        rw_tot = int(bc.rw_staffed_unit_days or 0)
        gr_tot = int(bc.gr_staffed_unit_days or 0)
        # Legacy: only totals set → treat as day-only
        if rw_d + rw_n == 0 and rw_tot > 0:
            rw_d = rw_tot
        if gr_d + gr_n == 0 and gr_tot > 0:
            gr_d = gr_tot
        rw_sum = rw_d + rw_n
        gr_sum = gr_d + gr_n
        rw_denom = int(cfg.rw_total_unit_days)
        gr_denom = int(cfg.gr_total_unit_days)
        rw_pct = float(rw_sum) / float(rw_denom) if rw_denom else 0.0
        gr_pct = float(gr_sum) / float(gr_denom) if gr_denom else 0.0
        base_metrics[bc.base_name] = {
            "rw_pct": rw_pct,
            "gr_pct": gr_pct,
            "rw_staffed": float(rw_sum),
            "gr_staffed": float(gr_sum),
            "rw_d": float(rw_d),
            "rw_n": float(rw_n),
            "gr_d": float(gr_d),
            "gr_n": float(gr_n),
        }
        rw_staffed += rw_sum
        gr_staffed += gr_sum

    system_rw_pct = (
        float(rw_staffed) / float(rw_total_unit_days) if rw_total_unit_days else 0.0
    )
    system_gr_pct = (
        float(gr_staffed) / float(SYSTEM_GR_MAX_SHIFTS_PER_WEEK)
        if SYSTEM_GR_MAX_SHIFTS_PER_WEEK > 0
        else 0.0
    )

    return WeekMetrics(
        week_start=str(ws.week_start),
        required_day=required_day,
        required_night=required_night,
        required_total=required_total,
        filled_day=filled_day,
        filled_night=filled_night,
        filled_total=filled_total,
        vacancies=vacancies,
        staffing_rate=staffing_rate,
        ot_shifts=total_ot,
        ot_dependency=ot_dependency,
        leave_total=leave_total,
        leave_exposure=leave_exposure,
        overnights_below=int(ws.overnights_below),
        pilot_vacancies=int(ws.pilot_vacancies),
        rw_total_unit_days=rw_total_unit_days,
        gr_total_unit_days=gr_total_unit_days,
        rw_staffed_unit_days=rw_staffed,
        gr_staffed_unit_days=gr_staffed,
        system_rw_pct=system_rw_pct,
        system_gr_pct=system_gr_pct,
        base_metrics=base_metrics or None,
        day_staffing_rate=(
            float(filled_day) / float(required_day) if required_day else 0.0
        ),
        night_staffing_rate=(
            float(filled_night) / float(required_night) if required_night else 0.0
        ),
    )


def compute_period_rollups(metrics_list: list[WeekMetrics]) -> PeriodRollups | None:
    """
    Aggregate weekly metrics into period averages and pooled rates.

    Averages: arithmetic mean of each week's ratio (legacy dashboard behavior).
    Pooled: period totals divided by period denominators (e.g. sum OT / sum filled).
    """
    n = len(metrics_list)
    if not n:
        return None
    filled_total = sum(m.filled_total for m in metrics_list)
    ot_shifts = sum(m.ot_shifts for m in metrics_list)
    leave_total = sum(m.leave_total for m in metrics_list)
    required_total = n * REQUIRED_TOTAL
    person_shifts = n * TOTAL_PERSON_SHIFTS
    rw_staffed = sum(m.rw_staffed_unit_days for m in metrics_list)
    gr_staffed = sum(m.gr_staffed_unit_days for m in metrics_list)
    rw_denom = n * metrics_list[0].rw_total_unit_days
    gr_denom = n * SYSTEM_GR_MAX_SHIFTS_PER_WEEK
    return PeriodRollups(
        n_weeks=n,
        filled_total=filled_total,
        ot_shifts=ot_shifts,
        leave_total=leave_total,
        avg_staffing_rate=sum(m.staffing_rate for m in metrics_list) / n,
        avg_ot_dependency=sum(m.ot_dependency for m in metrics_list) / n,
        avg_leave_exposure=sum(m.leave_exposure for m in metrics_list) / n,
        avg_system_rw_pct=sum(m.system_rw_pct for m in metrics_list) / n,
        avg_system_gr_pct=sum(m.system_gr_pct for m in metrics_list) / n,
        pooled_staffing_rate=(
            float(filled_total) / float(required_total) if required_total else 0.0
        ),
        pooled_ot_dependency=(
            float(ot_shifts) / float(filled_total) if filled_total else 0.0
        ),
        pooled_leave_exposure=(
            float(leave_total) / float(person_shifts) if person_shifts else 0.0
        ),
        pooled_system_rw_pct=(float(rw_staffed) / float(rw_denom) if rw_denom else 0.0),
        pooled_system_gr_pct=(float(gr_staffed) / float(gr_denom) if gr_denom else 0.0),
        avg_day_staffing_rate=sum(m.day_staffing_rate for m in metrics_list) / n,
        avg_night_staffing_rate=sum(m.night_staffing_rate for m in metrics_list) / n,
    )


@dataclass
class RoleFill:
    """Worked person-shifts vs weekly capacity for one clinical role."""

    role: str
    label: str
    worked: int
    capacity: int
    rate: float


def compute_role_fill(session, week_starts: list[str]) -> list[RoleFill]:
    """Fill rate by role over the given weeks.

    Worked = staffed + OT person-shifts from ``weekly_person_shifts``,
    excluding opportunistic extra units (``EXTRA_UNIT_CODES``) that aren't
    part of the required-line capacity below; capacity = per-role weekly
    person-shift capacity × number of weeks.
    """
    from sqlalchemy import func

    n = len(week_starts)
    counts = dict.fromkeys(ROLE_CAPACITY_PER_WEEK, 0)
    if n:
        rows = (
            session.query(WeeklyPersonShift.role, func.count())
            .filter(
                WeeklyPersonShift.week_start.in_(week_starts),
                WeeklyPersonShift.event_type.in_(["staffed", "ot"]),
                WeeklyPersonShift.included_in_aggregates == 1,
                WeeklyPersonShift.unit_code.notin_(EXTRA_UNIT_CODES),
            )
            .group_by(WeeklyPersonShift.role)
            .all()
        )
        for role, count in rows:
            if role in counts:
                counts[role] = int(count)
    result = []
    for role, per_week in ROLE_CAPACITY_PER_WEEK.items():
        capacity = per_week * n
        worked = counts[role]
        result.append(
            RoleFill(
                role=role,
                label=ROLE_FILL_LABELS[role],
                worked=worked,
                capacity=capacity,
                rate=float(worked) / float(capacity) if capacity else 0.0,
            )
        )
    return result


def get_metric_value(metrics: WeekMetrics, metric_name: str) -> float | None:
    """Return the numeric value for a board KPI metric name."""
    mapping = {
        "Staffing Rate": metrics.staffing_rate,
        # Backfill Rate (aka OT Dependency)
        "OT Dependency": metrics.ot_dependency,
        "Backfill Rate": metrics.ot_dependency,
        "Shift Exception %": metrics.leave_exposure,
        "Overnights Below Coverage": float(metrics.overnights_below),
        "Pilot Vacancies": float(metrics.pilot_vacancies),
        "System RW Coverage %": metrics.system_rw_pct,
        "System GR Coverage %": metrics.system_gr_pct,
    }
    return mapping.get(metric_name)


def get_pooled_metric_value(rollups: PeriodRollups, metric_name: str) -> float | None:
    """Return pooled period rate for a board KPI metric name."""
    mapping = {
        "Staffing Rate": rollups.pooled_staffing_rate,
        "OT Dependency": rollups.pooled_ot_dependency,
        "Backfill Rate": rollups.pooled_ot_dependency,
        "Shift Exception %": rollups.pooled_leave_exposure,
        "System RW Coverage %": rollups.pooled_system_rw_pct,
        "System GR Coverage %": rollups.pooled_system_gr_pct,
    }
    return mapping.get(metric_name)
