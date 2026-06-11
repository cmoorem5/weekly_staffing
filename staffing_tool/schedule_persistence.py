"""
Persist full schedule import detail (audit, person events, OPS View, raw cells).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from .models import (
    ScheduleImport,
    ScheduleParseIssue,
    ScheduleRawCell,
    WeeklyOpsViewAssignment,
    WeeklyOpsViewDay,
    WeeklyPersonShift,
)
from .schedule_import import (
    PARSER_VERSION,
    ParseIssue,
    ShiftRecord,
    collect_schedule_raw_cells,
    parse_ops_view_detail,
    schedule_file_sha256,
    weekly_person_shift_mappings,
)
from .staff_roster import StaffRosterMatchIndex, sync_roster_from_import


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clear_week_detail_tables(session: Session, week_start: str) -> None:
    """Remove prior import detail for replace-on-import."""
    session.query(ScheduleParseIssue).filter(
        ScheduleParseIssue.week_start == week_start
    ).delete()
    session.query(ScheduleRawCell).filter(
        ScheduleRawCell.week_start == week_start
    ).delete()
    session.query(WeeklyOpsViewAssignment).filter(
        WeeklyOpsViewAssignment.week_start == week_start
    ).delete()
    session.query(WeeklyOpsViewDay).filter(
        WeeklyOpsViewDay.week_start == week_start
    ).delete()
    session.query(ScheduleImport).filter(
        ScheduleImport.week_start == week_start
    ).delete()
    session.query(WeeklyPersonShift).filter(
        WeeklyPersonShift.week_start == week_start
    ).delete()


def persist_schedule_import_detail(
    session: Session,
    *,
    week_start: str,
    upload_path: str,
    source_filename: str,
    records: list[ShiftRecord],
    issues: list[ParseIssue],
    staff_roster_index: StaffRosterMatchIndex | None = None,
    sync_roster: bool = True,
    archive_raw_cells: bool = True,
) -> tuple[ScheduleImport, int]:
    """
    Replace per-week import detail: audit row, person events, OPS View, issues,
    optional raw grid archive. Caller must flush ``weekly_staffing`` first.

    When ``sync_roster`` is true (default), new clinical names from ``records``
    are added to the staff roster before person-shift rows are written.
    Returns ``(import_row, roster_added_count)``.
    """
    roster_added = 0
    if sync_roster:
        roster_added, staff_roster_index = sync_roster_from_import(
            session,
            records,
            created_at=_utc_now_iso(),
        )
    elif staff_roster_index is None:
        from .staff_roster import staff_roster_index_from_session

        staff_roster_index = staff_roster_index_from_session(session)
    _clear_week_detail_tables(session, week_start)

    raw_cells: list[dict[str, object]] = []
    if archive_raw_cells:
        try:
            raw_cells = collect_schedule_raw_cells(upload_path, week_start)
        except Exception:
            raw_cells = []
    try:
        ops_days, ops_assignments = parse_ops_view_detail(upload_path, week_start)
    except Exception:
        ops_days, ops_assignments = [], []

    person_maps = weekly_person_shift_mappings(
        week_start,
        records,
        staff_roster_index,
    )

    import_row = ScheduleImport(
        week_start=week_start,
        source_filename=(source_filename or "")[:512],
        imported_at=_utc_now_iso(),
        file_path=upload_path[:1024],
        file_hash=schedule_file_sha256(upload_path),
        parser_version=PARSER_VERSION,
        record_count=len(records),
        issue_count=len(issues),
        person_event_count=len(person_maps),
        raw_cell_count=len(raw_cells),
    )
    session.add(import_row)
    session.flush()

    if person_maps:
        for row in person_maps:
            row["schedule_import_id"] = import_row.id
        session.bulk_insert_mappings(WeeklyPersonShift, person_maps)

    for day in ops_days:
        session.add(
            WeeklyOpsViewDay(
                week_start=week_start,
                day_date=day.day_date.isoformat(),
                base_name=day.base_name,
                rw_count=day.rw_count,
                gr_count=day.gr_count,
            )
        )

    for assign in ops_assignments:
        session.add(
            WeeklyOpsViewAssignment(
                week_start=week_start,
                day_date=assign.day_date.isoformat(),
                unit_code=assign.unit_code[:32],
                role=assign.role[:16],
                excel_row=assign.excel_row,
                person_display=assign.person_display[:256],
                raw_value=assign.raw_value[:128],
                is_staffed=1 if assign.is_staffed else 0,
            )
        )

    for issue in issues:
        session.add(
            ScheduleParseIssue(
                week_start=week_start,
                sheet=(issue.sheet or "")[:128],
                cell=(issue.cell or "")[:32],
                raw_value=(issue.raw_value or "")[:128],
                issue_type=(issue.issue_type or "")[:32],
                message=issue.message or "",
            )
        )

    if raw_cells:
        session.bulk_insert_mappings(ScheduleRawCell, raw_cells)

    return import_row, roster_added
