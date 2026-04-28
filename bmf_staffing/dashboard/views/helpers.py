"""Shared dashboard view helpers (DB path, uploads, archive paths)."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.http import Http404
from staffing_tool.db import init_db, session_scope
from staffing_tool.manager_roster import (
    default_manager_last_names_upper,
    manager_last_names_upper_from_session,
)
from staffing_tool.schedule_import import AggregatedWeek

BASES = ["Bedford", "Lawrence", "Mansfield", "Manchester", "Plymouth"]

# Shown in dashboard exports (CSV/XLSX metadata) so files self-describe FY rules.
FY_AND_PAY_PERIOD_POLICY_NOTE = (
    "FY: week 1 starts the Sunday on or before Sep 28; FY ends the day before the "
    "next FY start. Pay periods: 14-day windows starting each FY week-1 Sunday."
)


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

DB_PATH = getattr(settings, "STAFFING_DB_PATH", None)
OUTPUT_DIR = getattr(settings, "STAFFING_OUTPUT_DIR", None)


def _manager_last_names_upper_for_parse() -> frozenset[str]:
    """Roster for schedule parse: DB table, or built-in default if DB empty/unset."""
    if not DB_PATH:
        return default_manager_last_names_upper()
    with session_scope(DB_PATH) as session:
        names = manager_last_names_upper_from_session(session)
    return names if names else default_manager_last_names_upper()


# Uploaded schedule workbooks: `schedule_upload_<timestamp>.xlsx`
_SCHEDULE_UPLOAD_PREFIX = "schedule_upload_"


def _schedule_upload_dir() -> str:
    root_dir = os.path.dirname(DB_PATH) if DB_PATH else os.getcwd()
    return os.path.join(root_dir, "uploads")


def _cleanup_old_schedule_uploads(upload_dir: str) -> None:
    """
    Delete files older than STAFFING_UPLOAD_RETENTION_HOURS (default 24).
    Only removes names matching schedule_upload_*.xlsx.
    """
    hours = getattr(settings, "STAFFING_UPLOAD_RETENTION_HOURS", 24)
    try:
        hours_f = float(hours)
    except (TypeError, ValueError):
        hours_f = 24.0
    if hours_f <= 0:
        return
    cutoff = datetime.now(UTC).timestamp() - hours_f * 3600
    if not os.path.isdir(upload_dir):
        return
    for name in os.listdir(upload_dir):
        if not name.startswith(_SCHEDULE_UPLOAD_PREFIX) or not name.endswith(".xlsx"):
            continue
        path = os.path.join(upload_dir, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue


def _resolve_output_dir() -> str:
    """Directory for Excel exports; default next to DB or cwd ``output``."""
    if OUTPUT_DIR:
        return OUTPUT_DIR
    if DB_PATH:
        return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "output")
    return "output"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_local_request(request) -> bool:
    """
    Simple guardrail: allow only loopback requests.
    (ALLOWED_HOSTS already limits hostnames, but this adds a network boundary.)
    """
    ip = (request.META.get("REMOTE_ADDR") or "").strip()
    return ip in {"127.0.0.1", "::1"}


def _archive_dir_under_repo_root() -> Path:
    """
    Archive dir must be under repo root (same folder as staffing.db).
    Settings defines STAFFING_DB_PATH at WEEKLY_STAFFING_ROOT/staffing.db.
    """
    db_path = getattr(settings, "STAFFING_DB_PATH", None)
    if not db_path:
        raise Http404("Database path is not configured (STAFFING_DB_PATH).")
    return Path(db_path).resolve().parent / "archive"


def _ensure_db():
    """Create DB if missing; run migrations (e.g. add leave_brev) on existing DBs."""
    if DB_PATH:
        init_db(DB_PATH)


def _last_sunday():
    today = datetime.now().date()
    days_back = (today.weekday() + 1) % 7
    sun = today - timedelta(days=days_back)
    return sun.strftime("%Y-%m-%d")

