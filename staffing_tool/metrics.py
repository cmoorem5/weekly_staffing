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
# Display labels for the three clinical roles. Report modules import these
# rather than spelling the labels out, so the wording stays consistent
# across the weekly/monthly/quarterly tables and the dashboard exports.
ROLE_FILL_LABELS = {"RN": "RN", "MEDIC": "Paramedic", "EMT": "EMT"}

# Canonical base display order for report tables (weekly/monthly/quarterly).
BASE_DISPLAY_ORDER = ["Bedford", "Lawrence", "Manchester", "Mansfield", "Plymouth"]

# Opportunistic extra Bedford ambulance unit codes (staffed when available,
# not counted toward minimum) — excluded from role-fill "worked" counts so
# they don't inflate fill rate past the required-line capacity above.
EXTRA_UNIT_CODES = {"GR2", "NG2", "G9B"}

# First week (Sunday) the current number definitions apply to: week 1 of FY2027.
# Earlier weeks keep the numbers as they were reported (crew-based staffing rate,
# no reasons or derived pilot vacancies / overnights), because the schedule data
# before this was entered under older conventions and has not been re-checked.
# To bring older weeks in, go through their uploads first, then lower this date.
CURRENT_DEFINITIONS_FROM_WEEK = "2026-09-27"


def uses_current_definitions(week_start: str) -> bool:
    """True for a week the current definitions apply to (FY2027 onward)."""
    return str(week_start) >= CURRENT_DEFINITIONS_FROM_WEEK


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
    # System ground shifts staffed: the total across bases, capped at the week's
    # ground plan (``gr_planned_unit_days``).
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
    # Ground plan for the week: 28 (4 trucks x 7 days), less any truck-days off
    # the plan for "no vehicle". Denominator of ``system_gr_pct``.
    gr_planned_unit_days: int = SYSTEM_GR_MAX_SHIFTS_PER_WEEK
    # Planned vehicle-days (rotor wing + ground) taken off the week's plan because
    # a vehicle was out of service with no crew ("no vehicle"). The staffing
    # rate's denominator is ``required_total`` after this.
    plan_removed_unit_days: int = 0


def role_ot_totals(row: WeeklyStaffing) -> dict[str, int]:
    """Per-role OT shift counts for one week, keyed by ``ROLE_CAPACITY_PER_WEEK`` role.

    Same fallback as ``compute_week_metrics``: the day/night split when any of
    it is populated, else the legacy per-role aggregate columns (weeks
    imported before the split store OT only there). A week with only
    ``ot_shifts`` set can't be split by role and reports zeros here.
    """
    ws = cast(Any, row)
    split = {
        "RN": int(ws.ot_rn_day or 0) + int(ws.ot_rn_night or 0),
        "MEDIC": int(ws.ot_medic_day or 0) + int(ws.ot_medic_night or 0),
        "EMT": int(ws.ot_emt_day or 0) + int(ws.ot_emt_night or 0),
    }
    if any(split.values()):
        return split
    return {
        "RN": int(ws.ot_rn or 0),
        "MEDIC": int(ws.ot_medic or 0),
        "EMT": int(ws.ot_emt or 0),
    }


def weekly_leave_total(row: WeeklyStaffing) -> int:
    """Shift exceptions for one week (PFML counts as LOA; includes JURY and BREV)."""
    ws = cast(Any, row)
    return (
        int(ws.leave_at or 0)
        + int(ws.leave_lt or 0)
        + int(ws.leave_sick or 0)
        + int(ws.leave_loa or 0)
        + int(ws.leave_pfml or 0)
        + int(ws.leave_jury or 0)
        + int(ws.leave_brev or 0)
    )


def coverages_by_week(
    session, week_starts: list[str]
) -> dict[str, list[WeeklyBaseCoverage]]:
    """``WeeklyBaseCoverage`` rows for many weeks in one query, keyed by week_start."""
    out: dict[str, list[WeeklyBaseCoverage]] = {ws: [] for ws in week_starts}
    if week_starts:
        for c in (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start.in_(week_starts))
            .all()
        ):
            out.setdefault(str(c.week_start), []).append(c)
    return out


def vehicle_filled_shifts(
    rw_day: int, rw_night: int, gr_day: int, gr_night: int, rw_plan: int, gr_plan: int
) -> tuple[int, int]:
    """(filled_day, filled_night): vehicles fully staffed, each type capped at its plan.

    Rotor wing is capped at ``rw_plan`` and ground at ``gr_plan`` (any ground
    shift counts, so a spare truck one day makes up a short day). Nights are
    filled first when splitting the capped totals, so day + night always equals
    the rotor wing plus ground counted. Shared by ``compute_week_metrics`` and
    the week edit save so the page and the reports can never disagree.
    """
    rw_counted = min(rw_day + rw_night, rw_plan)
    gr_counted = min(gr_day + gr_night, gr_plan)
    gr_night_part = min(gr_night, gr_counted)
    rw_night_part = min(rw_night, rw_counted)
    filled_night = rw_night_part + gr_night_part
    filled_day = (rw_counted - rw_night_part) + (gr_counted - gr_night_part)
    return filled_day, filled_night


def compute_week_metrics(
    row: WeeklyStaffing,
    base_coverages: list[WeeklyBaseCoverage],
    base_configs: list[BaseConfig],
) -> WeekMetrics:
    """Compute all KPIs for a single week."""
    # ORM instance: class-level Column types confuse static analysis
    ws = cast(Any, row)

    # Planned vehicles are fixed: 12 a day (5 rotor wing + 3 ground days, 3 rotor
    # wing + 1 ground nights) = 84 a week. Filled shifts are settled below, from
    # the base coverage, once the vehicle counts are known.
    required_day = REQUIRED_DAY
    required_night = REQUIRED_NIGHT
    required_total = REQUIRED_TOTAL
    filled_day = int(ws.filled_day)
    filled_night = int(ws.filled_night)

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

    leave_total = weekly_leave_total(row)
    leave_exposure = (
        float(leave_total) / float(TOTAL_PERSON_SHIFTS) if TOTAL_PERSON_SHIFTS else 0.0
    )

    base_by_name = {b.base_name: b for b in base_configs}
    rw_total_unit_days = sum(int(b.rw_total_unit_days) for b in base_configs)
    gr_total_unit_days = sum(int(b.gr_total_unit_days) for b in base_configs)

    base_metrics: dict[str, dict[str, float]] = {}
    rw_staffed = 0
    gr_staffed = 0
    rw_removed_total = 0
    rw_day_total = rw_night_total = gr_day_total = gr_night_total = 0

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
        # Aircraft out of service with no crew come off the plan ("no vehicle").
        rw_removed = int(getattr(bc, "rw_planned_removed", 0) or 0)
        rw_removed_total += rw_removed
        rw_denom = max(0, int(cfg.rw_total_unit_days) - rw_removed)
        gr_denom = int(cfg.gr_total_unit_days)
        rw_pct = float(rw_sum) / float(rw_denom) if rw_denom else 0.0
        gr_pct = float(gr_sum) / float(gr_denom) if gr_denom else 0.0
        base_metrics[bc.base_name] = {
            "rw_pct": rw_pct,
            "gr_pct": gr_pct,
            "rw_staffed": float(rw_sum),
            "rw_planned": float(rw_denom),
            "gr_staffed": float(gr_sum),
            "rw_d": float(rw_d),
            "rw_n": float(rw_n),
            "gr_d": float(gr_d),
            "gr_n": float(gr_n),
        }
        rw_staffed += rw_sum
        gr_staffed += gr_sum
        rw_day_total += rw_d
        rw_night_total += rw_n
        gr_day_total += gr_d
        gr_night_total += gr_n

    rw_total_unit_days = max(0, rw_total_unit_days - rw_removed_total)
    system_rw_pct = (
        float(rw_staffed) / float(rw_total_unit_days) if rw_total_unit_days else 0.0
    )

    # Ground is a system target (3 day + 1 night a day = 28 a week, any base;
    # crews move between bases). The weekly figure is the total of every ground
    # shift staffed across the bases, so a fifth truck one day offsets a short
    # day, capped at the week's plan (less any "no vehicle" truck-days).
    tgt_planned = getattr(ws, "gr_target_planned", None)
    gr_planned = (
        int(tgt_planned) if tgt_planned is not None else SYSTEM_GR_MAX_SHIFTS_PER_WEEK
    )
    gr_target_staffed = min(gr_staffed, gr_planned)
    system_gr_pct = float(gr_target_staffed) / float(gr_planned) if gr_planned else 0.0
    # Each base's share of the ground shifts actually staffed (cost-center view).
    for entry in base_metrics.values():
        entry["gr_share"] = entry["gr_staffed"] / gr_staffed if gr_staffed else 0.0

    # Filled shifts = vehicles fully staffed (crew plus pilot or EMT), the same
    # rule as RW and GR coverage, so the headline staffing rate always agrees
    # with them and can never pass 100%: rotor wing is capped at its plan and
    # ground at its weekly plan. A vehicle out of service with no crew is off
    # the plan, not a gap. Ground days and nights are split by filling nights
    # first, so day + night always equals the ground total counted.
    plan_removed = 0
    # Weeks before the cutoff keep the stored crew-based counts (as reported).
    if base_metrics and uses_current_definitions(str(ws.week_start)):
        filled_day, filled_night = vehicle_filled_shifts(
            rw_day_total,
            rw_night_total,
            gr_day_total,
            gr_night_total,
            rw_total_unit_days,
            gr_planned,
        )
        plan_removed = rw_removed_total + (SYSTEM_GR_MAX_SHIFTS_PER_WEEK - gr_planned)
        required_total = REQUIRED_TOTAL - plan_removed
    filled_total = filled_day + filled_night
    vacancies = max(0, required_total - filled_total)
    staffing_rate = (
        float(filled_total) / float(required_total) if required_total else 0.0
    )
    ot_dependency = float(total_ot) / float(filled_total) if filled_total else 0.0

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
        gr_staffed_unit_days=gr_target_staffed,
        system_rw_pct=system_rw_pct,
        system_gr_pct=system_gr_pct,
        base_metrics=base_metrics or None,
        day_staffing_rate=(
            float(filled_day) / float(required_day) if required_day else 0.0
        ),
        night_staffing_rate=(
            float(filled_night) / float(required_night) if required_night else 0.0
        ),
        gr_planned_unit_days=gr_planned,
        plan_removed_unit_days=plan_removed,
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
    # Each week's own plan, so a vehicle out of service with no crew does not
    # count against the period.
    required_total = sum(m.required_total for m in metrics_list)
    person_shifts = n * TOTAL_PERSON_SHIFTS
    rw_staffed = sum(m.rw_staffed_unit_days for m in metrics_list)
    gr_staffed = sum(m.gr_staffed_unit_days for m in metrics_list)
    rw_denom = sum(m.rw_total_unit_days for m in metrics_list)
    gr_denom = sum(m.gr_planned_unit_days for m in metrics_list)
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


def _role_fill_list(counts: dict[str, int], n_weeks: int) -> list[RoleFill]:
    result = []
    for role, per_week in ROLE_CAPACITY_PER_WEEK.items():
        capacity = per_week * n_weeks
        worked = counts.get(role, 0)
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


def compute_role_fill_by_week(
    session, week_starts: list[str]
) -> dict[str, list[RoleFill]]:
    """``compute_role_fill`` for each week separately, in a single query.

    Callers that bucket weeks (the staffing dashboard) pool these per-week
    results; one query here replaces one ``compute_role_fill`` call per week.
    """
    counts: dict[str, dict[str, int]] = {
        ws: dict.fromkeys(ROLE_CAPACITY_PER_WEEK, 0) for ws in week_starts
    }
    if week_starts:
        rows = (
            session.query(
                WeeklyPersonShift.id,
                WeeklyPersonShift.role,
                WeeklyPersonShift.week_start,
                WeeklyPersonShift.shift_date,
                WeeklyPersonShift.source_tab,
                WeeklyPersonShift.source_cell,
            )
            .filter(
                WeeklyPersonShift.week_start.in_(week_starts),
                WeeklyPersonShift.event_type.in_(["staffed", "ot"]),
                WeeklyPersonShift.included_in_aggregates == 1,
                WeeklyPersonShift.unit_code.notin_(EXTRA_UNIT_CODES),
            )
            .all()
        )
        seen: set[tuple] = set()
        for row_id, role, week_start, shift_date, source_tab, source_cell in rows:
            week_counts = counts.get(week_start)
            if week_counts is None or role not in week_counts:
                continue
            # One worked shift per grid cell; rows without cell provenance
            # (manually backfilled detail) still count individually.
            if source_cell:
                key = (role, week_start, shift_date, source_tab or "", source_cell)
            else:
                key = (role, "row", row_id)
            if key in seen:
                continue
            seen.add(key)
            week_counts[role] += 1
    return {ws: _role_fill_list(c, 1) for ws, c in counts.items()}


def compute_role_fill(session, week_starts: list[str]) -> list[RoleFill]:
    """Fill rate by role over the given weeks.

    Worked = staffed + OT seat-shifts from ``weekly_person_shifts``, counted
    once per source grid cell — capacity below is seats (e.g. EMT 49 = 7
    required lines × 7 days), and EMT partner rows list two people for one
    cell, so ``weekly_person_shift_mappings`` writes two rows for a single
    seat-day. Opportunistic extra units (``EXTRA_UNIT_CODES``) that aren't
    part of the required-line capacity are excluded; capacity = per-role
    weekly seat capacity × number of weeks.
    """
    unique = list(dict.fromkeys(week_starts))
    totals = dict.fromkeys(ROLE_CAPACITY_PER_WEEK, 0)
    for fills in compute_role_fill_by_week(session, unique).values():
        for rf in fills:
            totals[rf.role] += rf.worked
    return _role_fill_list(totals, len(week_starts))


def get_metric_value(metrics: WeekMetrics, metric_name: str) -> float | None:
    """Return the numeric value for a headline KPI metric name."""
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
    """Return pooled period rate for a headline KPI metric name."""
    mapping = {
        "Staffing Rate": rollups.pooled_staffing_rate,
        "OT Dependency": rollups.pooled_ot_dependency,
        "Backfill Rate": rollups.pooled_ot_dependency,
        "Shift Exception %": rollups.pooled_leave_exposure,
        "System RW Coverage %": rollups.pooled_system_rw_pct,
        "System GR Coverage %": rollups.pooled_system_gr_pct,
    }
    return mapping.get(metric_name)
