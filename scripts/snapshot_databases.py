#!/usr/bin/env python3
"""Copy the live databases into archive/ as the "latest" snapshot.

Once the databases live outside the program folder (see
relocate_databases.py) OneDrive no longer sees them, so the launcher runs this
when Crew Hub starts and again when its window closes. archive/ does sync, so
the off-machine copy is never older than the last time the app was used.

Each run replaces two files: archive/staffing_latest.db and
archive/crew_hub_latest.sqlite3. It uses SQLite's backup API, which gives a
consistent copy even if the app is mid-write, and writes to a temporary name
first so a half-written snapshot never replaces a good one. Does nothing while
the databases are still in the program folder (they are already synced).
Never raises: a failed snapshot must not stop the app from opening.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from staffing_tool.paths import (
    ARCHIVE_DIR,
    crew_hub_db_path,
    data_dir,
    staffing_db_path,
)

STAFFING_SNAPSHOT = "staffing_latest.db"
# .sqlite3 on purpose: the restore picker globs archive/*.db.
CREW_HUB_SNAPSHOT = "crew_hub_latest.sqlite3"


def snapshot_one(src: Path, dest: Path) -> bool:
    """Back ``src`` up to ``dest`` atomically. False when ``src`` is missing."""
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(f"{src.resolve().as_uri()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(tmp)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    os.replace(tmp, dest)
    return True


def snapshot_all(archive_dir: Path | None = None) -> list[Path]:
    """Snapshot both databases; returns the files written."""
    archive_dir = archive_dir or ARCHIVE_DIR
    written = []
    for src, name in (
        (staffing_db_path(), STAFFING_SNAPSHOT),
        (crew_hub_db_path(), CREW_HUB_SNAPSHOT),
    ):
        try:
            if snapshot_one(src, archive_dir / name):
                written.append(archive_dir / name)
        except (OSError, sqlite3.Error) as exc:
            print(f"Snapshot of {src.name} failed: {exc}", file=sys.stderr)
    return written


def main() -> int:
    try:
        if data_dir() is None:
            return 0
        snapshot_all()
    except Exception as exc:  # noqa: BLE001
        print(f"Snapshot skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
