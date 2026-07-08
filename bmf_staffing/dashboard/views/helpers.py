"""Shared dashboard view helpers (DB path, uploads, archive paths)."""

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from staffing_tool.db import DEFAULT_BASES, ensure_db_ready, session_scope
from staffing_tool.manager_roster import (
    default_manager_last_names_upper,
    manager_last_names_upper_from_session,
)
from staffing_tool.staff_roster import (
    StaffRosterMatchIndex,
    staff_roster_index_from_session,
)

logger = logging.getLogger(__name__)

# Canonical base order comes from DEFAULT_BASES so the dashboard and the
# staffing_tool importer never disagree about which bases exist.
BASES = [name for name, _rw, _gr in DEFAULT_BASES]

# Shown in dashboard exports (CSV/XLSX metadata) so files self-describe FY rules.
FY_AND_PAY_PERIOD_POLICY_NOTE = (
    "FY: week 1 starts the Sunday on or before Sep 28; FY ends the day before the "
    "next FY start. Pay periods: 14-day windows starting each FY week-1 Sunday."
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


def _staff_roster_index_for_import() -> StaffRosterMatchIndex:
    """Active staff roster for person-shift import (may be empty)."""
    if not DB_PATH:
        return StaffRosterMatchIndex()
    with session_scope(DB_PATH) as session:
        return staff_roster_index_from_session(session)


# Uploaded schedule workbooks: `schedule_upload_<timestamp>.xlsx`
_SCHEDULE_UPLOAD_PREFIX = "schedule_upload_"


def _schedule_upload_dir() -> str:
    root_dir = os.path.dirname(DB_PATH) if DB_PATH else os.getcwd()
    return os.path.join(root_dir, "uploads")


def _is_uploaded_schedule_path(path: str) -> bool:
    """
    True only if ``path`` is an existing schedule upload under the uploads dir.

    Guards the apply/preview flow against path traversal: the upload path is
    posted back as a hidden form field, so a crafted request could otherwise
    point the importer at an arbitrary ``.xlsx`` on the server. We require the
    resolved path to live inside ``uploads/`` and match the upload naming.
    """
    if not path:
        return False
    upload_dir = Path(_schedule_upload_dir()).resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(upload_dir)
    except (OSError, ValueError):
        return False
    return (
        resolved.is_file()
        and resolved.name.startswith(_SCHEDULE_UPLOAD_PREFIX)
        and resolved.suffix.lower() == ".xlsx"
    )


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


def serve_download(path: str | None, content_type: str | None = None) -> FileResponse:
    """Return a file-attachment response, or raise Http404 if the export is missing.

    Shared by the weekly/monthly/quarterly report download views, which all
    produce a path on disk and then stream it back as an attachment.
    """
    if not path or not os.path.isfile(path):
        raise Http404("Export file not found")
    return FileResponse(
        open(path, "rb"),
        as_attachment=True,
        filename=os.path.basename(path),
        content_type=content_type,
    )


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
    """Ensure staffing.db is initialized once per process (migrations, seed)."""
    if DB_PATH:
        ensure_db_ready(DB_PATH)


def backup_staffing_db_before_write() -> Path | None:
    """Snapshot staffing.db into archive/ before a destructive write.

    Never raises: a backup failure is logged and reported to the caller as
    ``None`` rather than blocking the user's import or delete.
    """
    if not DB_PATH or not os.path.isfile(DB_PATH):
        return None
    keep = getattr(settings, "STAFFING_BACKUP_KEEP", 30)
    try:
        from staffing_tool.db_backup import create_db_backup

        return create_db_backup(
            DB_PATH,
            archive_dir=_archive_dir_under_repo_root(),
            keep=keep,
        )
    except Exception:
        logger.warning("Pre-write backup of staffing.db failed", exc_info=True)
        return None


def staffing_db_health(db_path: str | None = None) -> dict[str, object]:
    """Snapshot for Settings health panel: paths, import markers, row counts."""
    path = db_path or DB_PATH
    health: dict[str, object] = {
        "db_path": path,
        "db_exists": bool(path and os.path.isfile(path)),
        **staffing_db_snapshot(path),
        "week_count": 0,
        "manager_shift_count": 0,
    }
    if not path or not health["db_exists"]:
        return health
    from staffing_tool.models import WeeklyManagerShift, WeeklyStaffing

    try:
        with session_scope(path) as session:
            health["week_count"] = session.query(WeeklyStaffing).count()
            health["manager_shift_count"] = session.query(WeeklyManagerShift).count()
            from staffing_tool.data_quality import audit_kpi_data_quality

            health["data_quality"] = audit_kpi_data_quality(session)
    except Exception:
        logger.warning("staffing_db_health query failed for %s", path, exc_info=True)
    return health


def staffing_db_snapshot(db_path: str | None = None) -> dict[str, str | None]:
    """
    Latest week and last schedule-import markers from staffing.db.

    Used by the operations banner and reports hub.
    """
    path = db_path or DB_PATH
    empty: dict[str, str | None] = {
        "latest_week_start": None,
        "latest_updated_at": None,
        "last_import_week_start": None,
        "last_import_updated_at": None,
    }
    if not path:
        return empty
    from staffing_tool.models import WeeklyStaffing

    try:
        with session_scope(path) as session:
            latest = (
                session.query(WeeklyStaffing)
                .order_by(WeeklyStaffing.week_start.desc())
                .first()
            )
            if latest:
                empty["latest_week_start"] = latest.week_start
                empty["latest_updated_at"] = latest.updated_at
            imported = (
                session.query(WeeklyStaffing)
                .filter(
                    (WeeklyStaffing.entered_by == "import")
                    | (WeeklyStaffing.notes.ilike("imported from schedule%"))
                )
                .order_by(
                    WeeklyStaffing.updated_at.desc(),
                    WeeklyStaffing.week_start.desc(),
                )
                .first()
            )
            if imported:
                empty["last_import_week_start"] = imported.week_start
                empty["last_import_updated_at"] = imported.updated_at
    except Exception:
        logger.warning("staffing_db_snapshot query failed for %s", path, exc_info=True)
    return empty


# URL names that show the operations import-status banner (see context_processors).
OPS_PAGE_URL_NAMES = frozenset(
    {
        "home",
        "week_list",
        "week_add",
        "week_edit",
        "week_delete",
        "import_schedule",
    }
)


def _last_sunday():
    today = datetime.now().date()
    days_back = (today.weekday() + 1) % 7
    sun = today - timedelta(days=days_back)
    return sun.strftime("%Y-%m-%d")
