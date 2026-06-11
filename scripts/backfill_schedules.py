#!/usr/bin/env python3
"""
Bulk-import historical schedule Excel files into staffing.db.

Scans one or more folders for .xlsx workbooks, detects week_start from sheet
headers (same as dashboard Import schedule), and runs the full import pipeline.

By default, weeks that already have a schedule_imports row are skipped so
manually fixed dashboard imports are never overwritten. Use --force to replace.

Use --upgrade-detail to re-process weeks that ALREADY exist (CEO aggregates
only) and populate person-level detail, manager AOC, OPS View, and import audit
without overwriting manual weekly_staffing fields.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staffing_tool.db import ensure_db_ready, init_db, session_scope
from staffing_tool.paths import PROJECT_ROOT
from staffing_tool.models import ScheduleImport, WeeklyStaffing
from staffing_tool.schedule_apply import (
    ScheduleApplyResult,
    apply_schedule_workbook,
    upgrade_week_detail,
    week_already_imported,
)
from staffing_tool.schedule_import import detect_schedule_week_starts
from staffing_tool.unit_mappings import resolve_unit_overrides

_FILENAME_WEEK_RE = re.compile(
    r"(?P<y>20\d{2})[-_]?(?P<m>\d{2})[-_]?(?P<d>\d{2})"
)


@dataclass(frozen=True)
class PlannedImport:
    week_start: str
    path: Path
    source_filename: str
    skip_reason: str | None = None


def _default_db_path() -> str:
    return str(PROJECT_ROOT / "staffing.db")


def _default_upload_dir(db_path: str) -> Path:
    return Path(os.path.dirname(os.path.abspath(db_path))) / "uploads"


def _week_in_date_range(
    week_start: str,
    *,
    from_date: str | None,
    to_date: str | None,
) -> bool:
    if from_date and week_start < from_date:
        return False
    if to_date and week_start > to_date:
        return False
    return True


def _collect_xlsx_files(directories: list[Path]) -> list[Path]:
    seen: dict[str, Path] = {}
    for directory in directories:
        if not directory.is_dir():
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith((".xlsx", ".xlsm")):
                continue
            path = directory / name
            if not path.is_file():
                continue
            key = str(path.resolve())
            seen[key] = path
    return sorted(seen.values(), key=lambda p: p.name.lower())


def _week_start_from_filename(path: Path) -> str | None:
    match = _FILENAME_WEEK_RE.search(path.stem)
    if not match:
        return None
    y, m, d = int(match.group("y")), int(match.group("m")), int(match.group("d"))
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{m:02d}-{d:02d}"


def _build_week_to_file(
    files: list[Path],
    *,
    force_week: str | None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> tuple[dict[str, Path], list[str]]:
    """Map week_start -> newest workbook path (one entry per week)."""
    errors: list[str] = []
    week_to_file: dict[str, tuple[Path, float]] = {}

    for path in files:
        weeks = _detect_weeks_for_file(path, force_week if len(files) == 1 else None)
        if not weeks:
            errors.append(f"{path.name}: could not detect week_start")
            continue
        mtime = path.stat().st_mtime
        for week in weeks:
            if not _week_in_date_range(week, from_date=from_date, to_date=to_date):
                continue
            prev = week_to_file.get(week)
            if prev is None or mtime >= prev[1]:
                week_to_file[week] = (path, mtime)

    return {week: path for week, (path, _mtime) in week_to_file.items()}, errors


def _detect_weeks_for_file(path: Path, force_week: str | None) -> list[str]:
    if force_week:
        return [force_week]
    try:
        detected = detect_schedule_week_starts(str(path))
    except Exception:
        detected = []
    if detected:
        return detected
    fallback = _week_start_from_filename(path)
    return [fallback] if fallback else []


def _resolve_workbook_for_week(
    session,
    week_start: str,
    week_to_file: dict[str, Path],
) -> tuple[Path | None, str]:
    """Prefer stored import path, then archive scan. Returns (path, source_filename)."""
    imp = (
        session.query(ScheduleImport)
        .filter(ScheduleImport.week_start == week_start)
        .first()
    )
    if imp and imp.file_path:
        stored = Path(imp.file_path)
        if stored.is_file():
            name = imp.source_filename or stored.name
            return stored, name

    path = week_to_file.get(week_start)
    if path is not None and path.is_file():
        return path, path.name

    return None, ""


def _list_existing_weeks_in_range(
    session,
    *,
    from_date: str | None,
    to_date: str | None,
) -> list[str]:
    weeks: set[str] = set()
    for (week_start,) in session.query(WeeklyStaffing.week_start).all():
        if _week_in_date_range(week_start, from_date=from_date, to_date=to_date):
            weeks.add(week_start)
    for (week_start,) in session.query(ScheduleImport.week_start).all():
        if _week_in_date_range(week_start, from_date=from_date, to_date=to_date):
            weeks.add(week_start)
    return sorted(weeks)


def plan_imports(
    files: list[Path],
    *,
    db_path: str,
    force: bool,
    force_week: str | None,
    from_date: str | None = None,
    to_date: str | None = None,
    upgrade_detail: bool = False,
) -> tuple[list[PlannedImport], list[str]]:
    """
    Build import plan: one entry per week_start (newest file wins duplicates).

    Default mode skips weeks already in schedule_imports. ``upgrade_detail`` targets
    existing weeks and requires a workbook in the archive or stored import path.
    """
    week_to_file, errors = _build_week_to_file(
        files,
        force_week=force_week,
        from_date=from_date,
        to_date=to_date,
    )

    planned: list[PlannedImport] = []
    with session_scope(db_path) as session:
        if upgrade_detail:
            for week in _list_existing_weeks_in_range(
                session, from_date=from_date, to_date=to_date
            ):
                path, source_name = _resolve_workbook_for_week(
                    session, week, week_to_file
                )
                if path is None:
                    errors.append(
                        f"Week {week}: no workbook found — add file to archive folder"
                    )
                    continue
                planned.append(
                    PlannedImport(
                        week_start=week,
                        path=path,
                        source_filename=source_name,
                        skip_reason=None,
                    )
                )
            return planned, errors

        for week in sorted(week_to_file):
            path = week_to_file[week]
            skip_reason = None
            if not force and week_already_imported(session, week):
                skip_reason = "already imported"
            planned.append(
                PlannedImport(
                    week_start=week,
                    path=path,
                    source_filename=path.name,
                    skip_reason=skip_reason,
                )
            )
    return planned, errors


def _print_summary(
    *,
    imported: list[ScheduleApplyResult],
    skipped: list[PlannedImport],
    apply_errors: list[str],
    detect_errors: list[str],
    roster_total: int,
    mapping_count: int,
    upgrade_detail: bool = False,
) -> None:
    print()
    title = "Upgrade detail summary" if upgrade_detail else "Backfill summary"
    print(f"=== {title} ===")
    action = "Upgraded" if upgrade_detail else "Imported"
    print(f"{action}: {len(imported)} week(s)")
    if not upgrade_detail:
        print(f"Skipped (already imported): {len(skipped)} week(s)")
    print(f"Errors: {len(apply_errors) + len(detect_errors)}")
    print(f"Roster additions: {roster_total}")
    if mapping_count:
        print(f"Unit mappings applied: {mapping_count}")

    if skipped and not upgrade_detail:
        print()
        print("Skipped weeks (use --force to replace):")
        for p in skipped:
            print(f"  - {p.week_start}  ({p.path.name})")

    needs_review = [r for r in imported if r.issue_count > 0]
    if needs_review:
        print()
        print("Weeks with parser issues (review in dashboard Import schedule):")
        for r in needs_review:
            print(f"  - {r.week_start}  ({r.issue_count} issue(s))")

    if detect_errors:
        print()
        label = "Missing workbooks" if upgrade_detail else "Detection issues"
        print(f"{label} ({len(detect_errors)}):")
        for msg in detect_errors:
            print(f"  - {msg}")

    if apply_errors:
        print()
        print("Apply errors:")
        for msg in apply_errors:
            print(f"  ERROR: {msg}", file=sys.stderr)


def run_backfill(
    directories: list[Path],
    *,
    db_path: str,
    dry_run: bool,
    force: bool,
    force_week: str | None,
    from_date: str | None = None,
    to_date: str | None = None,
    unit_map_paths: list[Path] | None = None,
    upgrade_detail: bool = False,
) -> int:
    ensure_db_ready(db_path)
    files = _collect_xlsx_files(directories)
    if not files:
        print("No .xlsx files found.", file=sys.stderr)
        return 1

    planned, detect_errors = plan_imports(
        files,
        db_path=db_path,
        force=force,
        force_week=force_week,
        from_date=from_date,
        to_date=to_date,
        upgrade_detail=upgrade_detail,
    )

    to_run = [p for p in planned if p.skip_reason is None]
    skipped = [p for p in planned if p.skip_reason is not None]

    print(f"Found {len(files)} workbook(s) in {len(directories)} folder(s).")
    if upgrade_detail:
        print("Mode: upgrade detail on existing weeks (manual CEO fields preserved).")
    if from_date or to_date:
        span = f"{from_date or '…'} through {to_date or '…'}"
        print(f"Date filter: {span}")
    if detect_errors:
        label = "Missing workbooks" if upgrade_detail else "Detection issues"
        print(f"{label} ({len(detect_errors)}):")
        for msg in detect_errors:
            print(f"  - {msg}")

    if skipped and not upgrade_detail:
        print(f"Skipping {len(skipped)} week(s) (use --force to replace):")
        for p in skipped:
            print(f"  skipped (already imported): {p.week_start}  {p.path.name}")

    if not to_run:
        print("Nothing to import." if not upgrade_detail else "Nothing to upgrade.")
        _print_summary(
            imported=[],
            skipped=skipped,
            apply_errors=[],
            detect_errors=detect_errors,
            roster_total=0,
            mapping_count=0,
            upgrade_detail=upgrade_detail,
        )
        return 0 if not detect_errors else 1

    unit_overrides: dict[str, str] = {}
    with session_scope(db_path) as session:
        extra = [str(p) for p in (unit_map_paths or [])]
        unit_overrides = resolve_unit_overrides(session, extra_csv_paths=extra)
    if unit_overrides:
        print(f"Using {len(unit_overrides)} unit code mapping(s).")

    if dry_run:
        print(f"Would import {len(to_run)} week(s):")
        for p in to_run:
            print(f"  - {p.week_start}  <-  {p.path}")
        _print_summary(
            imported=[],
            skipped=skipped,
            apply_errors=[],
            detect_errors=detect_errors,
            roster_total=0,
            mapping_count=len(unit_overrides),
            upgrade_detail=upgrade_detail,
        )
        return 0

    results: list[ScheduleApplyResult] = []
    apply_errors: list[str] = []
    roster_total = 0
    apply_fn = upgrade_week_detail if upgrade_detail else apply_schedule_workbook
    entered_by = "upgrade-detail" if upgrade_detail else "backfill"
    action_verb = "Upgraded" if upgrade_detail else "Imported"

    with session_scope(db_path) as session:
        for p in to_run:
            result, err = apply_fn(
                session,
                week_start=p.week_start,
                upload_path=str(p.path),
                source_filename=p.source_filename,
                unit_overrides=unit_overrides or None,
                entered_by=entered_by,
            )
            if err:
                apply_errors.append(f"{p.week_start} ({p.path.name}): {err}")
                continue
            assert result is not None
            results.append(result)
            roster_total += result.roster_added
            issue_note = ""
            if result.issue_count:
                issue_note = f", {result.issue_count} issue(s)"
            print(
                f"{action_verb} {result.week_start} from {p.path.name} "
                f"({result.record_count} cells, roster +{result.roster_added}"
                f"{issue_note})"
            )

    _print_summary(
        imported=results,
        skipped=skipped,
        apply_errors=apply_errors,
        detect_errors=detect_errors,
        roster_total=roster_total,
        mapping_count=len(unit_overrides),
        upgrade_detail=upgrade_detail,
    )

    return 0 if not apply_errors and not detect_errors else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Bulk-import schedule Excel files into staffing.db. "
            "By default, skips weeks that already have a schedule_imports row "
            "(manually fixed dashboard imports are preserved). Use --force to replace."
        ),
    )
    parser.add_argument(
        "--db",
        default=_default_db_path(),
        help="Path to staffing.db (default: staffing.db in repo root).",
    )
    parser.add_argument(
        "--dir",
        action="append",
        dest="dirs",
        metavar="PATH",
        help="Folder to scan for .xlsx (repeatable). Default: uploads/ next to DB.",
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Force week_start (only when a single ambiguous file is scanned).",
    )
    parser.add_argument(
        "--from-date",
        metavar="YYYY-MM-DD",
        help="Only import weeks on or after this Sunday week_start.",
    )
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        help="Only import weeks on or before this Sunday week_start.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List imports without writing to the database.",
    )
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="Skip weeks already in schedule_imports (default behavior).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-import and replace weeks that already have a schedule_imports row.",
    )
    parser.add_argument(
        "--upgrade-detail",
        action="store_true",
        help=(
            "Re-process EXISTING weeks to add person-level detail, manager AOC, "
            "OPS View, and import audit. Preserves manual weekly_staffing fields "
            "(unpartnered counts, targets, notes). Requires workbooks in --dir."
        ),
    )
    parser.add_argument(
        "--unit-map",
        action="append",
        dest="unit_maps",
        metavar="PATH",
        help=(
            "CSV with raw,maps_to columns (merged with DB mappings and "
            "data/unit_mappings.csv if present)."
        ),
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Create staffing.db if missing before importing.",
    )
    args = parser.parse_args(argv)

    if args.only_missing and args.force:
        parser.error("--only-missing and --force cannot be used together.")
    if args.upgrade_detail and args.force:
        parser.error("--upgrade-detail and --force cannot be used together.")
    if args.upgrade_detail and args.only_missing:
        parser.error("--upgrade-detail replaces --only-missing; do not combine them.")

    db_path = os.path.abspath(args.db)
    if args.init_db and not os.path.isfile(db_path):
        init_db(db_path)

    directories: list[Path] = []
    if args.dirs:
        directories.extend(Path(d).resolve() for d in args.dirs)
    else:
        directories.append(_default_upload_dir(db_path))

    unit_map_paths = [Path(p).resolve() for p in (args.unit_maps or [])]

    sys.exit(
        run_backfill(
            directories,
            db_path=db_path,
            dry_run=args.dry_run,
            force=args.force,
            force_week=args.week,
            from_date=args.from_date,
            to_date=args.to_date,
            unit_map_paths=unit_map_paths or None,
            upgrade_detail=args.upgrade_detail,
        )
    )


if __name__ == "__main__":
    main()
