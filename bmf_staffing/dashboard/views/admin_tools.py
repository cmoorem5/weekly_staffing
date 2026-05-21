"""Localhost-only backup / restore DB admin tools."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .helpers import _archive_dir_under_repo_root, _is_local_request


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
                {"src_path": str(src), "archive_dir": str(archive_dir), "backups": backups},
            )
        if confirm != "RESTORE":
            messages.error(
                request, 'Type RESTORE to confirm overwriting the current database.'
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
