"""Timestamped SQLite backups for staffing.db.

Snapshots the database before destructive operations (a schedule import
re-writes a whole week; deleting a week) so a bad import is always one copy
away from recovery. Auto-backups use a distinct prefix and are pruned to the
most recent ``keep`` files, so manual backups (``staffing_backup_*.db`` from
the admin tool / .bat) are never deleted.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

AUTO_BACKUP_PREFIX = "staffing_autobackup_"
DEFAULT_KEEP = 30


def archive_dir_for(db_path: str | Path) -> Path:
    """Default archive directory: ``archive/`` next to the database file."""
    return Path(db_path).resolve().parent / "archive"


def _prune_old_backups(dest_dir: Path, *, prefix: str, keep: int) -> None:
    if keep <= 0:
        return
    backups = sorted(
        (p for p in dest_dir.glob(f"{prefix}*.db") if p.is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Could not prune old backup %s", old, exc_info=True)


def create_db_backup(
    db_path: str | Path,
    *,
    archive_dir: str | Path | None = None,
    keep: int = DEFAULT_KEEP,
    prefix: str = AUTO_BACKUP_PREFIX,
) -> Path | None:
    """Copy the SQLite DB to ``archive/`` with a timestamped name.

    Returns the backup path, or ``None`` if the source DB does not exist.
    Prunes older files matching ``prefix`` down to the ``keep`` most recent.
    """
    src = Path(db_path).resolve()
    if not src.is_file():
        return None

    dest_dir = Path(archive_dir).resolve() if archive_dir else archive_dir_for(src)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = dest_dir / f"{prefix}{timestamp}.db"
    # Two backups in the same second would collide; disambiguate with a suffix.
    suffix = 1
    while dest.exists():
        dest = dest_dir / f"{prefix}{timestamp}_{suffix}.db"
        suffix += 1

    shutil.copy2(src, dest)
    _prune_old_backups(dest_dir, prefix=prefix, keep=keep)
    return dest
