"""Roll parsed shift records up into weekly aggregates and person shifts."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from .schedule_ops_view import _cap_base_coverage_split
from .schedule_types import (
    AggregatedWeek,
    DailyDetailDay,
    DayNight,
    ServiceType,
    ShiftRecord,
    _shift_record_persons,
)
from .staff_roster import (
    StaffRosterMatchIndex,
    canonical_display,
    match_parsed_person_to_roster,
)


def _person_shift_event_type(rec: ShiftRecord) -> str | None:
    """Derive staffed / leave / ot / training / skipped from a parsed shift record."""
    if rec.skip_reason == "training":
        return "training"
    if rec.skip_reason:
        return "skipped"
    if rec.leave_type:
        return "leave"
    if rec.filled:
        return "ot" if rec.overtime else "staffed"
    return None


def weekly_person_shift_mappings(
    week_start: str,
    records: Iterable[ShiftRecord],
    staff_roster_index: StaffRosterMatchIndex | None = None,
    *,
    schedule_import_id: int | None = None,
) -> list[dict[str, object]]:
    """
    Rows for ``WeeklyPersonShift`` bulk insert: staffed, leave, OT, and skipped
    cells for clinical roles (RN, MEDIC, EMT).

    When ``staff_roster_index`` is provided, ``staff_member_id`` and canonical
    ``person_display`` are set when a roster match exists; unmatched names are
    still persisted.
    """
    rows: list[dict[str, object]] = []
    for r in records:
        event_type = _person_shift_event_type(r)
        if event_type is None:
            continue
        if r.role not in {"RN", "MEDIC", "EMT"}:
            continue
        persons = _shift_record_persons(r)
        if not persons:
            if event_type != "skipped":
                continue
            persons = ("",)
        base_row = {
            "week_start": week_start,
            "schedule_import_id": schedule_import_id,
            "shift_date": r.date.isoformat(),
            "role": r.role,
            "event_type": event_type,
            "base_name": (r.base or "").strip(),
            "service_type": (r.service_type or "").strip(),
            "day_night": (r.day_night or "").strip() or "D",
            "unit_code": (r.unit_code or "").strip(),
            "leave_type": (r.leave_type or "").strip() or None,
            "overtime": 1 if r.overtime else 0,
            "raw_value": (r.raw_value or "")[:64],
            "source_tab": (r.source_tab or "")[:128],
            "source_cell": (r.source_cell or "")[:16],
            "excel_row": r.excel_row or 0,
            "excel_col": r.excel_col or 0,
            "is_manager_row": 1 if r.is_manager_row else 0,
            "included_in_aggregates": 1 if r.included_in_aggregates else 0,
            "skip_reason": (r.skip_reason or "")[:32] or None,
        }
        for person in persons:
            staff_member_id = None
            display = person[:256]
            if staff_roster_index is not None and person:
                entry = match_parsed_person_to_roster(
                    person, r.role, staff_roster_index
                )
                if entry is not None:
                    staff_member_id = entry.id
                    display = canonical_display(entry)[:256]
            rows.append(
                {
                    **base_row,
                    "staff_member_id": staff_member_id,
                    "person_display": display,
                }
            )
    return rows


def weekly_manager_shift_mappings(
    week_start: str,
    records: Iterable[ShiftRecord],
) -> list[dict[str, object]]:
    """
    Rows for ``WeeklyManagerShift`` bulk insert: staffed unit cells and AOC
    admin days on manager roster rows (same last-name set as leave exclusion).
    """
    rows: list[dict[str, object]] = []
    for r in records:
        if not r.is_manager_row:
            continue
        if r.manager_event_type == "aoc":
            if r.role not in {"RN", "MEDIC", "EMT"}:
                continue
            rows.append(
                {
                    "week_start": week_start,
                    "person_display": (r.person_display or "").strip() or "(unknown)",
                    "role": r.role,
                    "shift_date": r.date.isoformat(),
                    "event_type": "aoc",
                    "base_name": "",
                    "service_type": "",
                    "day_night": "",
                    "unit_code": "",
                    "overtime": 0,
                    "raw_value": (r.raw_value or "")[:64],
                    "source_tab": (r.source_tab or "")[:128],
                    "source_cell": (r.source_cell or "")[:16],
                }
            )
            continue
        if not r.filled:
            continue
        if r.role not in {"RN", "MEDIC", "EMT"}:
            continue
        if not (r.base or "").strip():
            continue
        rows.append(
            {
                "week_start": week_start,
                "person_display": (r.person_display or "").strip() or "(unknown)",
                "role": r.role,
                "shift_date": r.date.isoformat(),
                "event_type": "line_shift",
                "base_name": r.base,
                "service_type": r.service_type,
                "day_night": r.day_night,
                "unit_code": (r.unit_code or "").strip(),
                "overtime": 1 if r.overtime else 0,
                "raw_value": (r.raw_value or "")[:64],
                "source_tab": (r.source_tab or "")[:128],
                "source_cell": (r.source_cell or "")[:16],
            }
        )
    return rows


def aggregate_week_from_records(
    week_start: str,
    records: Iterable[ShiftRecord],
    ops_coverage: tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]
    | None = None,
    ops_daily: dict[date, tuple[int, int]] | None = None,
) -> AggregatedWeek:
    """
    Aggregate parsed shift records into the fields needed for WeeklyStaffing
    and WeeklyBaseCoverage.

    - Filled Day/Night (crew shifts): for each (date, base, service_type, D/N,
      unit_code) where both RN and MEDIC are staffed, count one filled crew shift.
      If OPS View is present but the grid pairs nothing, filled day/night fall back to
      the sum of staffed RW/GR unit-days from OPS (matches base coverage).
    - OT by role + Day/Night: count overtime shifts (person-shifts).
    - Leave totals: AT, LT, SICK, LOA (PFML folded into LOA), JURY, BREV.
    - Base coverage: when ops_coverage is provided, use
      (rw_day, rw_night, gr_day, gr_night) from OPS View; legacy 2-tuple
      (rw_tot, gr_tot) is treated as all day / zero night.
      Otherwise derive from shift records: one RW unit-day per slot with RN+Medic;
      one GR unit-day per slot with RN+Medic+EMT (same rules as OPS View).
    """
    filled_day = filled_night = 0
    ot_rn_day = ot_rn_night = 0
    ot_medic_day = ot_medic_night = 0
    ot_emt_day = ot_emt_night = 0
    leave_at = leave_lt = leave_sick = leave_loa = leave_jury = leave_brev = 0
    training_total = 0
    leave_breakdown: dict[tuple[str, str], int] = {}
    base_rw_day: dict[str, int] = {}
    base_rw_night: dict[str, int] = {}
    base_gr_day: dict[str, int] = {}
    base_gr_night: dict[str, int] = {}
    # (date, base, service_type, day_night, slot) -> role flags for pairing.
    # slot = unit_code when set, else source_cell so distinct lines do not collapse.
    # EMT is included so GR unit-days match OPS View (RN + Medic + EMT).
    crew_roles: dict[tuple[date, str, ServiceType, DayNight, str], dict[str, bool]] = {}
    try:
        week_start_date = datetime.strptime(week_start, "%Y-%m-%d").date()
    except ValueError:
        week_start_date = None
    week_days: list[date] = []
    daily_filled: dict[date, int] = {}
    daily_rw: dict[date, int] = {}
    daily_gr: dict[date, int] = {}
    daily_exc: dict[date, int] = {}
    if week_start_date is not None:
        week_days = [week_start_date + timedelta(days=i) for i in range(7)]
        daily_filled = {d: 0 for d in week_days}
        daily_rw = {d: 0 for d in week_days}
        daily_gr = {d: 0 for d in week_days}
        daily_exc = {d: 0 for d in week_days}

    for rec in records:
        if not rec.included_in_aggregates:
            continue
        if rec.skip_reason == "training":
            training_total += 1
            continue
        # Filled staffing: crew slots (RN + MEDIC paired); when not using OPS
        # View, derive base-level staffed unit-days from the same keys (below).
        if rec.filled and rec.day_night in {"D", "N"}:
            if rec.role in {"RN", "MEDIC", "EMT"} and rec.base and rec.service_type:
                slot = (rec.unit_code or "").strip() or rec.source_cell
                key = (rec.date, rec.base, rec.service_type, rec.day_night, slot)
                info = crew_roles.setdefault(
                    key, {"RN": False, "MEDIC": False, "EMT": False}
                )
                info[rec.role] = True

            # OT by role and day/night (person-shifts)
            if rec.overtime:
                if rec.role == "RN":
                    if rec.day_night == "D":
                        ot_rn_day += 1
                    else:
                        ot_rn_night += 1
                elif rec.role == "MEDIC":
                    if rec.day_night == "D":
                        ot_medic_day += 1
                    else:
                        ot_medic_night += 1
                elif rec.role == "EMT":
                    if rec.day_night == "D":
                        ot_emt_day += 1
                    else:
                        ot_emt_night += 1

        # Leave/absence totals and per-role breakdown (leave_type display).
        lt = rec.leave_type
        if lt and rec.date in daily_exc:
            daily_exc[rec.date] += 1
        if lt == "AT":
            leave_at += 1
        elif lt in ("LT-D", "LT-N", "LT"):
            leave_lt += 1
        elif lt == "SICK":
            leave_sick += 1
        elif lt in ("LOA", "PFML"):
            leave_loa += 1
        elif lt == "JURY":
            leave_jury += 1
        elif lt in ("BREV", "BERV", "BEREAVEMENT"):
            leave_brev += 1
        if lt:
            if rec.role == "MEDIC":
                role_display = "Medic"
            elif rec.role == "PILOT":
                role_display = "Pilot"
            else:
                role_display = rec.role
            display_type = "BREV" if lt in ("BREV", "BERV", "BEREAVEMENT") else lt
            key = (role_display, display_type)
            leave_breakdown[key] = leave_breakdown.get(key, 0) + 1

    # Convert crew_roles into filled crew shifts by day/night (RN + Medic only).
    for (day_date, _base_name, service_type, dn, _slot), roles in crew_roles.items():
        if roles.get("RN") and roles.get("MEDIC"):
            if dn == "D":
                filled_day += 1
            else:
                filled_night += 1
            if day_date in daily_filled:
                daily_filled[day_date] += 1
        if day_date in daily_rw:
            if service_type == "RW" and roles.get("RN") and roles.get("MEDIC"):
                daily_rw[day_date] += 1
            elif (
                service_type == "GR"
                and roles.get("RN")
                and roles.get("MEDIC")
                and roles.get("EMT")
            ):
                daily_gr[day_date] += 1

    # Base coverage without OPS View: one unit-day per staffed slot (not per person-row).
    # Aligns with OPS View — RW: RN+Medic; GR: RN+Medic+EMT.
    if ops_coverage is None:
        for (_d, base_name, service_type, dn, _slot), roles in crew_roles.items():
            if service_type == "RW":
                if not (roles.get("RN") and roles.get("MEDIC")):
                    continue
                if dn == "N":
                    base_rw_night[base_name] = base_rw_night.get(base_name, 0) + 1
                else:
                    base_rw_day[base_name] = base_rw_day.get(base_name, 0) + 1
            elif service_type == "GR":
                if not (roles.get("RN") and roles.get("MEDIC") and roles.get("EMT")):
                    continue
                if dn == "N":
                    base_gr_night[base_name] = base_gr_night.get(base_name, 0) + 1
                else:
                    base_gr_day[base_name] = base_gr_day.get(base_name, 0) + 1

    if ops_coverage is not None:
        if len(ops_coverage) >= 4:
            base_rw_day = dict(ops_coverage[0])
            base_rw_night = dict(ops_coverage[1])
            base_gr_day = dict(ops_coverage[2])
            base_gr_night = dict(ops_coverage[3])
        else:
            base_rw_day = dict(ops_coverage[0])
            base_rw_night = {}
            base_gr_day = dict(ops_coverage[1])
            base_gr_night = {}
    # Cap both sources at the per-base weekly maxima. Opportunistic extra
    # vehicles (e.g. Bedford GR2/NG2) can push OPS View counts past the
    # configured plan, which would report >100% base coverage and inflate
    # the fixed-denominator system GR %.
    _cap_base_coverage_split(
        base_rw_day,
        base_rw_night,
        base_gr_day,
        base_gr_night,
    )

    # Grid often has names or layout drift; OPS View still counts staffed vehicles.
    if (
        ops_coverage is not None
        and len(ops_coverage) >= 4
        and filled_day == 0
        and filled_night == 0
    ):
        ops_day = sum(base_rw_day.values()) + sum(base_gr_day.values())
        ops_night = sum(base_rw_night.values()) + sum(base_gr_night.values())
        if ops_day > 0 or ops_night > 0:
            filled_day = ops_day
            filled_night = ops_night

    all_rw = set(base_rw_day) | set(base_rw_night)
    all_gr = set(base_gr_day) | set(base_gr_night)
    base_rw = {b: base_rw_day.get(b, 0) + base_rw_night.get(b, 0) for b in all_rw}
    base_gr = {b: base_gr_day.get(b, 0) + base_gr_night.get(b, 0) for b in all_gr}

    if ops_daily:
        for day_date, (rw, gr) in ops_daily.items():
            if day_date in daily_rw:
                daily_rw[day_date] = rw
                daily_gr[day_date] = gr

    if week_days and sum(daily_filled.values()) == 0 and ops_daily:
        for day_date, (rw, gr) in ops_daily.items():
            if day_date in daily_filled:
                daily_filled[day_date] = rw + gr

    daily_detail = [
        DailyDetailDay(
            day_date=day_date,
            filled=daily_filled.get(day_date, 0),
            rw=daily_rw.get(day_date, 0),
            gr=daily_gr.get(day_date, 0),
            exceptions=daily_exc.get(day_date, 0),
        )
        for day_date in week_days
    ]

    return AggregatedWeek(
        week_start=week_start,
        filled_day=filled_day,
        filled_night=filled_night,
        ot_rn_day=ot_rn_day,
        ot_rn_night=ot_rn_night,
        ot_medic_day=ot_medic_day,
        ot_medic_night=ot_medic_night,
        ot_emt_day=ot_emt_day,
        ot_emt_night=ot_emt_night,
        leave_at=leave_at,
        leave_lt=leave_lt,
        leave_sick=leave_sick,
        leave_loa=leave_loa,
        leave_jury=leave_jury,
        leave_brev=leave_brev,
        training_total=training_total,
        leave_breakdown=leave_breakdown,
        base_rw_staffed=base_rw,
        base_gr_staffed=base_gr,
        base_rw_staffed_day=base_rw_day,
        base_rw_staffed_night=base_rw_night,
        base_gr_staffed_day=base_gr_day,
        base_gr_staffed_night=base_gr_night,
        daily_detail=daily_detail,
    )
