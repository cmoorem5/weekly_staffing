"""
Apply a parsed schedule workbook to staffing.db (dashboard Apply equivalent).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .db import DEFAULT_BASES
from .manager_roster import (
    default_manager_last_names_upper,
    manager_last_names_upper_from_session,
)
from .models import (
    ScheduleImport,
    WeeklyBaseCoverage,
    WeeklyDailyDetail,
    WeeklyLeaveDetail,
    WeeklyManagerShift,
    WeeklyStaffing,
)
from .schedule_import import (
    PARSER_VERSION,
    AggregatedWeek,
    aggregate_week_from_records,
    parse_ops_view_daily,
    parse_schedule_workbook,
    weekly_manager_shift_mappings,
)
from .schedule_persistence import persist_schedule_import_detail

BASES = [name for name, _rw, _gr in DEFAULT_BASES]


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ops_coverage_total(
    ops: tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]] | None,
) -> int:
    if not ops:
        return 0
    return sum(sum(d.values()) for d in ops)


def _agg_leave_total(agg: AggregatedWeek) -> int:
    return (
        agg.leave_at
        + agg.leave_lt
        + agg.leave_sick
        + agg.leave_loa
        + agg.leave_jury
        + getattr(agg, "leave_brev", 0)
    )


def manager_last_names_upper_for_parse(session: Session | None) -> frozenset[str]:
    if session is None:
        return default_manager_last_names_upper()
    names = manager_last_names_upper_from_session(session)
    return names if names else default_manager_last_names_upper()


IMPORTED_FROM_SCHEDULE_NOTE = "Imported from schedule"


@dataclass(frozen=True)
class WeeklyStaffingManualFields:
    """CEO week fields edited manually — preserved on detail upgrade."""

    day_target: int
    night_min: int
    overnights_below: int
    pilot_vacancies: int
    medic_unpartnered: int
    rn_unpartnered_staff: int
    unpartnered_note_medic: str | None
    unpartnered_note_rn: str | None
    notes: str | None
    entered_by: str | None
    created_at: str | None


@dataclass(frozen=True)
class ScheduleApplyResult:
    week_start: str
    roster_added: int
    record_count: int
    filled_day: int
    filled_night: int
    issue_count: int = 0


def capture_weekly_staffing_manual_fields(
    row: WeeklyStaffing | None,
) -> WeeklyStaffingManualFields | None:
    """Snapshot manual CEO fields before re-import."""
    if row is None:
        return None
    notes = row.notes
    if notes and notes.strip() == IMPORTED_FROM_SCHEDULE_NOTE:
        notes = None
    return WeeklyStaffingManualFields(
        day_target=row.day_target,
        night_min=row.night_min,
        overnights_below=row.overnights_below,
        pilot_vacancies=row.pilot_vacancies,
        medic_unpartnered=row.medic_unpartnered,
        rn_unpartnered_staff=row.rn_unpartnered_staff,
        unpartnered_note_medic=row.unpartnered_note_medic,
        unpartnered_note_rn=row.unpartnered_note_rn,
        notes=notes,
        entered_by=row.entered_by,
        created_at=row.created_at,
    )


def restore_weekly_staffing_manual_fields(
    row: WeeklyStaffing,
    preserved: WeeklyStaffingManualFields | None,
) -> None:
    """Restore manual CEO fields after aggregate re-import."""
    if preserved is None:
        return
    row.day_target = preserved.day_target
    row.night_min = preserved.night_min
    row.overnights_below = preserved.overnights_below
    row.pilot_vacancies = preserved.pilot_vacancies
    row.medic_unpartnered = preserved.medic_unpartnered
    row.rn_unpartnered_staff = preserved.rn_unpartnered_staff
    row.unpartnered_note_medic = preserved.unpartnered_note_medic
    row.unpartnered_note_rn = preserved.unpartnered_note_rn
    if preserved.notes is not None:
        row.notes = preserved.notes
    if preserved.entered_by is not None:
        row.entered_by = preserved.entered_by
    if preserved.created_at is not None:
        row.created_at = preserved.created_at


def week_already_imported(session: Session, week_start: str) -> bool:
    """True when any schedule_imports row exists for this week."""
    row = (
        session.query(ScheduleImport)
        .filter(ScheduleImport.week_start == week_start)
        .first()
    )
    return row is not None


def week_has_current_import(session: Session, week_start: str) -> bool:
    row = (
        session.query(ScheduleImport)
        .filter(ScheduleImport.week_start == week_start)
        .first()
    )
    return row is not None and row.parser_version == PARSER_VERSION


def week_has_existing_data(session: Session, week_start: str) -> bool:
    """True when weekly_staffing or schedule_imports has this week."""
    if (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    ):
        return True
    return week_already_imported(session, week_start)


def apply_schedule_workbook(
    session: Session,
    *,
    week_start: str,
    upload_path: str,
    source_filename: str | None = None,
    unit_overrides: dict[str, str] | None = None,
    manager_last_names_upper: frozenset[str] | None = None,
    entered_by: str = "import",
    preserve_manual_fields: bool = False,
) -> tuple[ScheduleApplyResult | None, str | None]:
    """
    Parse and persist one week from a schedule workbook.

    Returns ``(result, None)`` on success or ``(None, error_message)`` on failure.
    Caller must use an open SQLAlchemy session; commits are not performed here.
    """
    mgr_names = manager_last_names_upper
    if mgr_names is None:
        mgr_names = manager_last_names_upper_for_parse(session)

    try:
        records, issues, ops_coverage = parse_schedule_workbook(
            upload_path,
            week_start=week_start,
            unit_overrides=unit_overrides,
            manager_last_names_upper=mgr_names,
        )
    except Exception as exc:
        return None, f"Error parsing workbook: {exc}"

    if not records:
        mismatch = next((i for i in issues if i.issue_type == "week_mismatch"), None)
        if mismatch:
            return None, mismatch.message
        return None, "No usable shifts found in schedule file."

    ops_daily = (
        parse_ops_view_daily(upload_path, week_start)
        if ops_coverage is not None
        else None
    )

    agg: AggregatedWeek = aggregate_week_from_records(
        week_start,
        records,
        ops_coverage=ops_coverage,
        ops_daily=ops_daily,
    )

    if (
        agg.filled_day == 0
        and agg.filled_night == 0
        and _agg_leave_total(agg) == 0
        and _ops_coverage_total(ops_coverage) == 0
    ):
        dates = sorted({r.date for r in records})
        span = f"{dates[0].isoformat()} through {dates[-1].isoformat()}" if dates else "(none)"
        return (
            None,
            "Import produced no crew shifts, schedule exceptions, or OPS View "
            f"coverage for week {week_start}. Parsed cells covered dates {span}.",
        )

    ot_total = (
        agg.ot_rn_day
        + agg.ot_rn_night
        + agg.ot_medic_day
        + agg.ot_medic_night
        + agg.ot_emt_day
        + agg.ot_emt_night
    )

    now = _utc_now_iso()
    notes = IMPORTED_FROM_SCHEDULE_NOTE

    row = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    preserved_manual = (
        capture_weekly_staffing_manual_fields(row) if preserve_manual_fields else None
    )
    if row:
        row.filled_day = agg.filled_day
        row.filled_night = agg.filled_night
        row.ot_shifts = ot_total
        row.ot_rn = agg.ot_rn_day + agg.ot_rn_night
        row.ot_medic = agg.ot_medic_day + agg.ot_medic_night
        row.ot_emt = agg.ot_emt_day + agg.ot_emt_night
        row.ot_rn_day = agg.ot_rn_day
        row.ot_rn_night = agg.ot_rn_night
        row.ot_medic_day = agg.ot_medic_day
        row.ot_medic_night = agg.ot_medic_night
        row.ot_emt_day = agg.ot_emt_day
        row.ot_emt_night = agg.ot_emt_night
        row.leave_at = agg.leave_at
        row.leave_lt = agg.leave_lt
        row.leave_sick = agg.leave_sick
        row.leave_loa = agg.leave_loa
        row.leave_jury = getattr(agg, "leave_jury", 0)
        row.leave_brev = getattr(agg, "leave_brev", 0)
        row.notes = notes
        row.updated_at = now
    else:
        session.add(
            WeeklyStaffing(
                week_start=week_start,
                day_target=8,
                night_min=4,
                filled_day=agg.filled_day,
                filled_night=agg.filled_night,
                ot_shifts=ot_total,
                ot_rn=agg.ot_rn_day + agg.ot_rn_night,
                ot_medic=agg.ot_medic_day + agg.ot_medic_night,
                ot_emt=agg.ot_emt_day + agg.ot_emt_night,
                ot_rn_day=agg.ot_rn_day,
                ot_rn_night=agg.ot_rn_night,
                ot_medic_day=agg.ot_medic_day,
                ot_medic_night=agg.ot_medic_night,
                ot_emt_day=agg.ot_emt_day,
                ot_emt_night=agg.ot_emt_night,
                leave_at=agg.leave_at,
                leave_lt=agg.leave_lt,
                leave_sick=agg.leave_sick,
                leave_loa=agg.leave_loa,
                leave_jury=getattr(agg, "leave_jury", 0),
                leave_brev=getattr(agg, "leave_brev", 0),
                overnights_below=0,
                pilot_vacancies=0,
                notes=notes,
                entered_by=entered_by,
                created_at=now,
                updated_at=now,
            )
        )

    session.flush()

    session.query(WeeklyLeaveDetail).filter(
        WeeklyLeaveDetail.week_start == week_start
    ).delete()
    for (role, leave_type), count in getattr(agg, "leave_breakdown", {}).items():
        if count:
            session.add(
                WeeklyLeaveDetail(
                    week_start=week_start,
                    role=role,
                    leave_type=leave_type,
                    count=count,
                )
            )

    session.query(WeeklyDailyDetail).filter(
        WeeklyDailyDetail.week_start == week_start
    ).delete()
    for day in agg.daily_detail:
        session.add(
            WeeklyDailyDetail(
                week_start=week_start,
                day_date=day.day_date.isoformat(),
                filled=day.filled,
                rw=day.rw,
                gr=day.gr,
                exceptions=day.exceptions,
            )
        )

    session.query(WeeklyManagerShift).filter(
        WeeklyManagerShift.week_start == week_start
    ).delete()
    mgr_maps = weekly_manager_shift_mappings(week_start, records)
    if mgr_maps:
        session.bulk_insert_mappings(WeeklyManagerShift, mgr_maps)

    filename = source_filename or os.path.basename(upload_path)
    import_row, roster_added = persist_schedule_import_detail(
        session,
        week_start=week_start,
        upload_path=upload_path,
        source_filename=filename,
        records=records,
        issues=issues,
    )

    for base_name in BASES:
        rw_d = agg.base_rw_staffed_day.get(base_name, 0)
        rw_n = agg.base_rw_staffed_night.get(base_name, 0)
        gr_d = agg.base_gr_staffed_day.get(base_name, 0)
        gr_n = agg.base_gr_staffed_night.get(base_name, 0)
        rw_s = rw_d + rw_n
        gr_s = gr_d + gr_n

        rec = (
            session.query(WeeklyBaseCoverage)
            .filter(
                WeeklyBaseCoverage.week_start == week_start,
                WeeklyBaseCoverage.base_name == base_name,
            )
            .first()
        )
        if rec:
            rec.rw_staffed_unit_days = rw_s
            rec.gr_staffed_unit_days = gr_s
            rec.rw_staffed_day = rw_d
            rec.rw_staffed_night = rw_n
            rec.gr_staffed_day = gr_d
            rec.gr_staffed_night = gr_n
        else:
            session.add(
                WeeklyBaseCoverage(
                    week_start=week_start,
                    base_name=base_name,
                    rw_staffed_unit_days=rw_s,
                    gr_staffed_unit_days=gr_s,
                    rw_staffed_day=rw_d,
                    rw_staffed_night=rw_n,
                    gr_staffed_day=gr_d,
                    gr_staffed_night=gr_n,
                )
            )

    if preserve_manual_fields and row is not None:
        restore_weekly_staffing_manual_fields(row, preserved_manual)
        row.updated_at = now
        session.flush()

    return (
        ScheduleApplyResult(
            week_start=week_start,
            roster_added=roster_added,
            record_count=len(records),
            filled_day=agg.filled_day,
            filled_night=agg.filled_night,
            issue_count=import_row.issue_count,
        ),
        None,
    )


def upgrade_week_detail(
    session: Session,
    *,
    week_start: str,
    upload_path: str,
    source_filename: str | None = None,
    unit_overrides: dict[str, str] | None = None,
    manager_last_names_upper: frozenset[str] | None = None,
    entered_by: str = "upgrade-detail",
) -> tuple[ScheduleApplyResult | None, str | None]:
    """
    Re-parse an existing week to populate person/manager/OPS detail tables.

    CEO aggregates are refreshed from the workbook; manual week fields
    (unpartnered counts, targets, notes, etc.) are preserved.
    """
    return apply_schedule_workbook(
        session,
        week_start=week_start,
        upload_path=upload_path,
        source_filename=source_filename,
        unit_overrides=unit_overrides,
        manager_last_names_upper=manager_last_names_upper,
        entered_by=entered_by,
        preserve_manual_fields=True,
    )
