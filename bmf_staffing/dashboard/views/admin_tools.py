"""Localhost-only backup / restore DB admin tools."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from staffing_tool.db_backup import AUTO_BACKUP_PREFIX

from .helpers import _archive_dir_under_repo_root, _is_local_request

_MANUAL_BACKUP_PREFIX = "staffing_backup_"


def _human_size(num_bytes: int) -> str:
    """Compact human-readable file size (e.g. ``2.4 MB``)."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _parse_backup_timestamp(name: str) -> str:
    """Best-effort 'YYYY-MM-DD HH:MM UTC' from a backup filename, else ''."""
    stem = name[:-3] if name.endswith(".db") else name
    for prefix in (AUTO_BACKUP_PREFIX, _MANUAL_BACKUP_PREFIX):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
            break
    stem = stem.split("_", 1)[0]  # drop any collision suffix (_1, _2, ...)
    try:
        dt = datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _list_archive_backups(archive_dir: Path, db_name: str) -> list[dict]:
    """Backup files in ``archive/`` (newest first) with display metadata."""
    entries: list[dict] = []
    if not archive_dir.is_dir():
        return entries
    for p in sorted(archive_dir.glob("*.db"), reverse=True):
        if not p.is_file() or p.name == db_name:
            continue
        is_auto = p.name.startswith(AUTO_BACKUP_PREFIX)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        entries.append(
            {
                "name": p.name,
                "kind": "Automatic" if is_auto else "Manual",
                "is_auto": is_auto,
                "timestamp": _parse_backup_timestamp(p.name),
                "size": _human_size(size),
            }
        )
    return entries


def _safe_archive_backup_path(archive_dir: Path, selected: str) -> Path | None:
    """Resolve ``selected`` strictly inside ``archive/``; None if invalid."""
    if not selected or "/" in selected or "\\" in selected:
        return None
    candidate = (archive_dir / selected).resolve()
    archive_resolved = archive_dir.resolve()
    try:
        candidate.relative_to(archive_resolved)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


@require_http_methods(["GET", "POST"])
def backup_db(request):
    """
    Local-only admin tool: copy staffing.db to archive/ with a timestamped filename.
    Uses shutil.copy2 so it works without any .bat script.
    """
    if not _is_local_request(request):
        raise Http404("This admin tool is available only on localhost.")

    db_path = getattr(settings, "STAFFING_DB_PATH", None)
    if not db_path:
        raise Http404("Database path is not configured (STAFFING_DB_PATH).")
    src = Path(db_path).resolve()
    if not src.is_file():
        raise Http404(f"Staffing DB not found: {src}")

    archive_dir = _archive_dir_under_repo_root()

    if request.method == "POST":
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_name = f"staffing_backup_{timestamp}.db"
        dest = (archive_dir / backup_name).resolve()
        shutil.copy2(src, dest)
        return render(
            request,
            "dashboard/backup_db_confirm.html",
            {
                "src_path": str(src),
                "archive_dir": str(archive_dir),
                "backup_path": str(dest),
                "backup_name": backup_name,
                "did_backup": True,
            },
        )

    return render(
        request,
        "dashboard/backup_db_confirm.html",
        {
            "src_path": str(src),
            "archive_dir": str(archive_dir),
            "did_backup": False,
        },
    )


def _backup_db_to_archive(src: Path, archive_dir: Path) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"staffing_backup_{timestamp}.db"
    dest = (archive_dir / backup_name).resolve()
    shutil.copy2(src, dest)
    return dest


@require_http_methods(["GET", "POST"])
def restore_db(request):
    """
    Local-only admin tool: restore staffing.db from a backup file under archive/.
    Always creates a timestamped backup of the current DB before overwriting.
    """
    if not _is_local_request(request):
        raise Http404("This admin tool is available only on localhost.")

    db_path = getattr(settings, "STAFFING_DB_PATH", None)
    if not db_path:
        raise Http404("Database path is not configured (STAFFING_DB_PATH).")
    src = Path(db_path).resolve()
    if not src.is_file():
        raise Http404(f"Staffing DB not found: {src}")

    archive_dir = _archive_dir_under_repo_root()
    archive_dir.mkdir(parents=True, exist_ok=True)

    backups: list[str] = []
    for p in sorted(archive_dir.glob("*.db"), reverse=True):
        if p.name == src.name:
            continue
        backups.append(p.name)

    if request.method == "POST":
        selected = (request.POST.get("backup_name") or "").strip()
        confirm = (request.POST.get("confirm_text") or "").strip().upper()
        if not selected or selected not in backups:
            messages.error(request, "Please select a valid backup file from archive/.")
            return render(
                request,
                "dashboard/restore_db.html",
                {
                    "src_path": str(src),
                    "archive_dir": str(archive_dir),
                    "backups": backups,
                },
            )
        if confirm != "RESTORE":
            messages.error(
                request, "Type RESTORE to confirm overwriting the current database."
            )
            return render(
                request,
                "dashboard/restore_db.html",
                {
                    "src_path": str(src),
                    "archive_dir": str(archive_dir),
                    "backups": backups,
                    "selected": selected,
                },
            )
        backup_path = (archive_dir / selected).resolve()
        if not backup_path.is_file():
            raise Http404(f"Backup file not found: {backup_path}")
        archive_resolved = archive_dir.resolve()
        try:
            backup_path.relative_to(archive_resolved)
        except ValueError:
            messages.error(
                request,
                "Backup file is not inside archive/ (refusing restore for safety).",
            )
            return render(
                request,
                "dashboard/restore_db.html",
                {
                    "src_path": str(src),
                    "archive_dir": str(archive_dir),
                    "backups": backups,
                    "selected": selected,
                },
            )

        pre_backup = _backup_db_to_archive(src, archive_dir)
        shutil.copy2(backup_path, src)
        messages.success(
            request,
            f"Restored staffing.db from {selected}. "
            f"A backup of the prior DB was saved as {pre_backup.name}.",
        )
        return redirect("restore_db")

    return render(
        request,
        "dashboard/restore_db.html",
        {"src_path": str(src), "archive_dir": str(archive_dir), "backups": backups},
    )


@require_http_methods(["GET", "POST"])
def database_backups(request):
    """Localhost-only page: browse archive/ backups and one-click restore.

    Reuses the same archive path validation and pre-restore safety backup as
    ``restore_db`` so a mis-click is always recoverable.
    """
    if not _is_local_request(request):
        raise Http404("This admin tool is available only on localhost.")

    db_path = getattr(settings, "STAFFING_DB_PATH", None)
    if not db_path:
        raise Http404("Database path is not configured (STAFFING_DB_PATH).")
    src = Path(db_path).resolve()
    if not src.is_file():
        raise Http404(f"Staffing DB not found: {src}")

    archive_dir = _archive_dir_under_repo_root()
    archive_dir.mkdir(parents=True, exist_ok=True)

    if request.method == "POST":
        selected = (request.POST.get("backup_name") or "").strip()
        backup_path = _safe_archive_backup_path(archive_dir, selected)
        if backup_path is None:
            messages.error(
                request,
                "Could not restore: select a valid backup file from archive/.",
            )
            return redirect("database_backups")

        pre_backup = _backup_db_to_archive(src, archive_dir)
        shutil.copy2(backup_path, src)
        messages.success(
            request,
            f"Restored staffing.db from {selected}. "
            f"A safety backup of the prior database was saved to "
            f"archive/{pre_backup.name}.",
        )
        return redirect("database_backups")

    backups = _list_archive_backups(archive_dir, src.name)
    auto_count = sum(1 for b in backups if b["is_auto"])
    return render(
        request,
        "dashboard/database_backups.html",
        {
            "src_path": str(src),
            "archive_dir": str(archive_dir),
            "backups": backups,
            "backup_count": len(backups),
            "auto_count": auto_count,
            "manual_count": len(backups) - auto_count,
        },
    )
