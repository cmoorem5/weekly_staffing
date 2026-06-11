"""
Query helpers for persisted schedule import data (future reports).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .db import session_scope
from .models import (
    ScheduleImport,
    ScheduleParseIssue,
    ScheduleRawCell,
    WeeklyOpsViewAssignment,
    WeeklyOpsViewDay,
    WeeklyPersonShift,
)


def list_imports_for_week(
    week_start: str,
    db_path: str | None = None,
) -> list[ScheduleImport]:
    """Import audit rows for a week (0 or 1 after replace-on-import)."""
    with session_scope(db_path) as session:
        return (
            session.query(ScheduleImport)
            .filter(ScheduleImport.week_start == week_start)
            .order_by(ScheduleImport.imported_at.desc())
            .all()
        )


def get_week_person_events(
    week_start: str,
    db_path: str | None = None,
    *,
    event_type: str | None = None,
    role: str | None = None,
    include_skipped: bool = True,
    person_display: str | None = None,
) -> list[WeeklyPersonShift]:
    """Person-level schedule cells for one week."""
    with session_scope(db_path) as session:
        q = session.query(WeeklyPersonShift).filter(
            WeeklyPersonShift.week_start == week_start
        )
        if event_type:
            q = q.filter(WeeklyPersonShift.event_type == event_type)
        if role:
            q = q.filter(WeeklyPersonShift.role == role)
        if not include_skipped:
            q = q.filter(WeeklyPersonShift.event_type != "skipped")
        if person_display:
            q = q.filter(WeeklyPersonShift.person_display == person_display)
        return q.order_by(
            WeeklyPersonShift.shift_date,
            WeeklyPersonShift.person_display,
            WeeklyPersonShift.source_cell,
        ).all()


def get_week_ops_view_days(
    week_start: str,
    db_path: str | None = None,
    *,
    base_name: str | None = None,
) -> list[WeeklyOpsViewDay]:
    """OPS View per-day per-base staffed unit-day counts."""
    with session_scope(db_path) as session:
        q = session.query(WeeklyOpsViewDay).filter(
            WeeklyOpsViewDay.week_start == week_start
        )
        if base_name:
            q = q.filter(WeeklyOpsViewDay.base_name == base_name)
        return q.order_by(
            WeeklyOpsViewDay.day_date,
            WeeklyOpsViewDay.base_name,
        ).all()


def get_week_ops_view_assignments(
    week_start: str,
    db_path: str | None = None,
    *,
    unit_code: str | None = None,
    day_date: str | None = None,
) -> list[WeeklyOpsViewAssignment]:
    """OPS View name-level cells for a week."""
    with session_scope(db_path) as session:
        q = session.query(WeeklyOpsViewAssignment).filter(
            WeeklyOpsViewAssignment.week_start == week_start
        )
        if unit_code:
            q = q.filter(WeeklyOpsViewAssignment.unit_code == unit_code)
        if day_date:
            q = q.filter(WeeklyOpsViewAssignment.day_date == day_date)
        return q.order_by(
            WeeklyOpsViewAssignment.day_date,
            WeeklyOpsViewAssignment.unit_code,
            WeeklyOpsViewAssignment.role,
            WeeklyOpsViewAssignment.excel_row,
        ).all()


def get_week_all_cells(
    week_start: str,
    db_path: str | None = None,
    *,
    sheet_name: str | None = None,
) -> list[ScheduleRawCell]:
    """Raw grid archive for a week (empty if not archived or over limit)."""
    with session_scope(db_path) as session:
        q = session.query(ScheduleRawCell).filter(
            ScheduleRawCell.week_start == week_start
        )
        if sheet_name:
            q = q.filter(ScheduleRawCell.sheet_name == sheet_name)
        return q.order_by(
            ScheduleRawCell.sheet_name,
            ScheduleRawCell.row_idx,
            ScheduleRawCell.col_idx,
        ).all()


def get_week_parse_issues(
    week_start: str,
    db_path: str | None = None,
) -> list[ScheduleParseIssue]:
    """Parser issues retained for a week."""
    with session_scope(db_path) as session:
        return (
            session.query(ScheduleParseIssue)
            .filter(ScheduleParseIssue.week_start == week_start)
            .order_by(ScheduleParseIssue.id)
            .all()
        )


def load_import_summary(
    session: Session,
    week_start: str,
) -> ScheduleImport | None:
    """Latest import audit row for a week (within an open session)."""
    return (
        session.query(ScheduleImport)
        .filter(ScheduleImport.week_start == week_start)
        .order_by(ScheduleImport.imported_at.desc())
        .first()
    )
