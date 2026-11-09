#!/usr/bin/env python3
"""Move the two live databases out of the (OneDrive-synced) program folder. Once.

The program folder can sync through OneDrive. A sync client that copies a
SQLite file while the app is writing to it is a known way to end up with a
corrupted database or a "conflicted copy". The backups in archive/ are plain
finished files and stay where they are, which is what you want synced.

What it does, in this order, and it stops before changing anything if a check
fails:

1. Refuses to run while Crew Hub is open (port 8000 answers) or a database
   has a rollback journal beside it.
2. Copies staffing.db and bmf_staffing/db.sqlite3 into the data folder
   (default %LOCALAPPDATA%\\BMFStaffing\\data) and checks each copy: same
   size, and SQLite's own integrity check passes.
3. Writes BMF_STAFFING_DATA_DIR into .env, which is what the app reads.
4. Moves the originals into archive/ as *_before_move_<timestamp>. Nothing is
   deleted.

Safe to rerun: once .env has the setting it does nothing. It never overwrites a
database that is already in the data folder.

    python scripts/relocate_databases.py --dry-run
    python scripts/relocate_databases.py
"""

from __future__ import annotations

import argparse
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from staffing_tool.paths import (
    CREW_HUB_DB_NAME,
    DATA_DIR_ENV,
    STAFFING_DB_NAME,
    expand_data_dir,
)

DEFAULT_DATA_DIR = r"%LOCALAPPDATA%\BMFStaffing\data"


class RelocateError(Exception):
    """A check failed; nothing was changed."""


def _env_has_setting(env_path: Path) -> bool:
    if not env_path.is_file():
        return False
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if (
            line.strip().startswith(f"{DATA_DIR_ENV}=")
            and line.split("=", 1)[1].strip()
        ):
            return True
    return False


def _server_running(port: int = 8000) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _integrity_ok(path: Path) -> bool:
    """SQLite's own integrity check, read-only. False for anything unreadable."""
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            return conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _append_env(env_path: Path, raw_value: str) -> None:
    text = ""
    if env_path.is_file():
        with open(env_path, encoding="utf-8", newline="") as f:
            text = f.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    if text and not text.endswith(("\n", "\r")):
        text += newline
    text += (
        f"{newline}# Live databases are kept outside this (OneDrive-synced) folder."
        f"{newline}# Set by scripts/relocate_databases.py; backups stay in archive/."
        f"{newline}{DATA_DIR_ENV}={raw_value}{newline}"
    )
    with open(env_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def relocate(
    repo_root: Path,
    raw_data_dir: str,
    env_path: Path,
    *,
    dry_run: bool = False,
    check_server: bool = True,
    now: datetime | None = None,
) -> list[str]:
    """Move the databases. Returns plain lines saying what was done.

    Raises ``RelocateError`` (nothing changed) when a check fails.
    """
    if _env_has_setting(env_path):
        return ["Databases were already moved out of this folder; nothing to do."]
    data_dir = expand_data_dir(raw_data_dir)
    try:
        inside = data_dir.resolve().is_relative_to(repo_root.resolve())
    except OSError:
        inside = False
    if inside:
        raise RelocateError(
            f"{data_dir} is inside the program folder, so it would still sync."
        )
    if check_server and _server_running():
        raise RelocateError(
            "Crew Hub is open. Close its window (or run Stop_Crew_Hub.bat), "
            "then run this again."
        )

    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    archive = repo_root / "archive"
    plan = [
        (
            repo_root / STAFFING_DB_NAME,
            data_dir / STAFFING_DB_NAME,
            archive / f"staffing_before_move_{stamp}.db",
        ),
        (
            repo_root / "bmf_staffing" / CREW_HUB_DB_NAME,
            data_dir / CREW_HUB_DB_NAME,
            # .sqlite3 on purpose: the restore picker globs archive/*.db and
            # must never offer a Crew Hub database as a staffing.db.
            archive / f"crew_hub_before_move_{stamp}.sqlite3",
        ),
    ]
    for src, dest, _kept in plan:
        if not src.is_file():
            continue
        if dest.exists():
            raise RelocateError(
                f"{dest} already exists and so does {src}. Not guessing which "
                "one is current: move the one you do not want out of the way."
            )
        for suffix in ("-journal", "-wal"):
            if Path(str(src) + suffix).exists():
                raise RelocateError(
                    f"{src.name} has an unfinished write beside it ({suffix}). "
                    "Open and close Crew Hub once, then run this again."
                )
        if not _integrity_ok(src):
            raise RelocateError(
                f"{src} does not pass SQLite's integrity check. Restore a "
                "backup from archive/ before moving it."
            )

    lines: list[str] = []
    todo = [(s, d, k) for s, d, k in plan if s.is_file()]
    if dry_run:
        for src, dest, kept in todo:
            lines.append(f"Would move {src} -> {dest} (original kept as {kept.name})")
        lines.append(f"Would add {DATA_DIR_ENV}={raw_data_dir} to {env_path.name}")
        return lines

    data_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    try:
        for src, dest, _kept in todo:
            shutil.copy2(src, dest)
            copied.append(dest)
            if dest.stat().st_size != src.stat().st_size or not _integrity_ok(dest):
                raise RelocateError(f"The copy of {src.name} did not verify.")
    except (OSError, RelocateError) as exc:
        for dest in copied:
            dest.unlink(missing_ok=True)
        raise RelocateError(f"Could not copy the databases ({exc}).") from exc

    _append_env(env_path, raw_data_dir)
    lines.append(f"Databases now live in {data_dir}")
    archive.mkdir(parents=True, exist_ok=True)
    for src, _dest, kept in todo:
        try:
            shutil.move(str(src), str(kept))
            lines.append(f"Original {src.name} kept as archive/{kept.name}")
        except OSError:
            lines.append(
                f"WARNING: could not move the old {src} into archive/. It is no "
                "longer used; move or delete it by hand so it is not mistaken "
                "for the live database."
            )
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data-dir", default=DEFAULT_DATA_DIR)
    p.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    try:
        lines = relocate(
            PROJECT_ROOT, args.data_dir, Path(args.env), dry_run=args.dry_run
        )
    except (RelocateError, RuntimeError) as exc:
        print(f"      Databases were NOT moved: {exc}")
        return 1
    for line in lines:
        print(f"      {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
