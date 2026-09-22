#!/usr/bin/env python3
"""
Read-only audit: re-parse every already-imported schedule week through the
CURRENT parser and compare against what's stored in schedule_imports.

Makes no writes to staffing.db. Use this after changing anything in the
schedule-import pipeline (cell classification, code families, manager
credit, etc.) to see which historical weeks would now parse differently,
without touching the database the way --upgrade-detail does.

For each week already in schedule_imports (optionally filtered by
--from-date/--to-date or a single --week), it:
  1. Resolves the source workbook: the path stored on the import row first,
     falling back to a scan of --dir folder(s).
  2. Calls parse_schedule_workbook() directly (the same parse the dashboard
     and backfill_schedules.py use) and counts the resulting ParseIssues.
  3. Compares that count against the stored issue_count and flags weeks
     where the number or type of issues has changed.

Usage:
    python scripts/audit_schedule_imports.py --dir archive --dir uploads
    python scripts/audit_schedule_imports.py --week 2026-05-24
    python scripts/audit_schedule_imports.py --from-date 2026-01-01
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staffing_tool.db import ensure_db_ready, session_scope
from staffing_tool.models import ScheduleImport
from staffing_tool.paths import PROJECT_ROOT
from staffing_tool.schedule_apply import (
    manager_last_names_upper_for_parse,
    training_codes_upper_for_parse,
)
from staffing_tool.schedule_import import (
    detect_schedule_week_starts,
    parse_schedule_workbook,
)
from staffing_tool.unit_mappings import resolve_unit_overrides


def _default_db_path() -> str:
    return str(PROJECT_ROOT / "staffing.db")


def _week_in_range(
    week_start: str, *, from_date: str | None, to_date: str | None
) -> bool:
    if from_date and week_start < from_date:
        return False
    if to_date and week_start > to_date:
        return False
    return True


def _collect_xlsx_files(directories: list[Path]) -> list[Path]:
    files: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for name in sorted(directory.iterdir()):
            if name.is_file() and name.suffix.lower() in (".xlsx", ".xlsm"):
                files.append(name)
    return files


def _resolve_workbook(
    session,
    week_start: str,
    scanned: dict[str, Path],
) -> tuple[Path | None, str]:
    """Prefer the path recorded at import time; fall back to a scanned folder."""
    imp = (
        session.query(ScheduleImport)
        .filter(ScheduleImport.week_start == week_start)
        .first()
    )
    if imp and imp.file_path:
        stored = Path(imp.file_path)
        if stored.is_file():
            return stored, imp.source_filename or stored.name

    path = scanned.get(week_start)
    if path is not None:
        return path, path.name

    return None, ""


def _issue_type_counts(issues) -> Counter:
    return Counter(i.issue_type for i in issues)


def run_audit(
    *,
    db_path: str,
    directories: list[Path],
    week: str | None,
    from_date: str | None,
    to_date: str | None,
    max_examples: int,
) -> int:
    ensure_db_ready(db_path)

    scanned: dict[str, Path] = {}
    for path in _collect_xlsx_files(directories):
        try:
            weeks = detect_schedule_week_starts(str(path))
        except Exception:
            weeks = []
        for w in weeks:
            scanned[w] = path

    unresolved: list[str] = []
    parse_errors: list[tuple[str, str]] = []
    changed: list[dict] = []
    unchanged = 0

    with session_scope(db_path) as session:
        imported_weeks = {
            row.week_start: row for row in session.query(ScheduleImport).all()
        }

        if week:
            target_weeks = [week] if week in imported_weeks else []
            if not target_weeks:
                print(f"Week {week} has no schedule_imports row — nothing to audit.")
                return 1
        else:
            target_weeks = sorted(
                w
                for w in imported_weeks
                if _week_in_range(w, from_date=from_date, to_date=to_date)
            )

        if not target_weeks:
            print("No imported weeks match the given filters.")
            return 0

        mgr_names = manager_last_names_upper_for_parse(session)
        training_codes = training_codes_upper_for_parse(session)
        unit_overrides = resolve_unit_overrides(session)

        print(
            f"Auditing {len(target_weeks)} imported week(s) against the current parser..."
        )
        print()

        for w in target_weeks:
            stored = imported_weeks[w]
            path, _source_name = _resolve_workbook(session, w, scanned)
            if path is None:
                unresolved.append(w)
                continue

            try:
                _records, issues, _ops = parse_schedule_workbook(
                    str(path),
                    week_start=w,
                    unit_overrides=unit_overrides,
                    manager_last_names_upper=mgr_names,
                    extra_training_codes=training_codes,
                )
            except Exception as exc:
                parse_errors.append((w, str(exc)))
                continue

            new_count = len(issues)
            old_count = stored.issue_count
            if new_count == old_count:
                unchanged += 1
                continue

            changed.append(
                {
                    "week": w,
                    "old_count": old_count,
                    "new_count": new_count,
                    "types": _issue_type_counts(issues),
                    "examples": issues[:max_examples],
                    "file": path.name,
                }
            )

    print("=== Audit summary ===")
    print(f"Unchanged (issue count matches): {unchanged}")
    print(f"Changed issue count:              {len(changed)}")
    print(f"Workbook not found (skipped):     {len(unresolved)}")
    print(f"Parse errors:                      {len(parse_errors)}")

    if changed:
        print()
        print("Weeks with a different issue count under the current parser:")
        for c in sorted(changed, key=lambda c: c["week"]):
            delta = c["new_count"] - c["old_count"]
            sign = "+" if delta > 0 else ""
            print(
                f"  - {c['week']}  ({c['file']}): "
                f"{c['old_count']} -> {c['new_count']} issue(s) ({sign}{delta})"
            )
            if c["types"]:
                type_summary = ", ".join(
                    f"{t}={n}" for t, n in c["types"].most_common()
                )
                print(f"      by type: {type_summary}")
            for issue in c["examples"]:
                print(
                    f"      [{issue.issue_type}] {issue.sheet} {issue.cell}: {issue.message}"
                )

    if unresolved:
        print()
        print("Weeks skipped (no stored workbook path and none found in --dir):")
        for w in unresolved:
            print(f"  - {w}")

    if parse_errors:
        print()
        print("Weeks that raised an error while parsing:")
        for w, msg in parse_errors:
            print(f"  - {w}: {msg}")

    return 0 if not changed and not parse_errors else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit: re-parse already-imported schedule weeks through the "
            "current code and report any change in parser issues. Never writes to "
            "the database."
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
        default=[],
        help=(
            "Folder to scan for .xlsx/.xlsm as a fallback when a week's stored "
            "workbook path no longer exists (repeatable, e.g. --dir archive)."
        ),
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Audit a single week_start instead of the whole range.",
    )
    parser.add_argument(
        "--from-date",
        metavar="YYYY-MM-DD",
        help="Only audit weeks on or after this Sunday week_start.",
    )
    parser.add_argument(
        "--to-date",
        metavar="YYYY-MM-DD",
        help="Only audit weeks on or before this Sunday week_start.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=5,
        help="Max example issue messages to print per changed week (default 5).",
    )
    args = parser.parse_args(argv)

    exit_code = run_audit(
        db_path=args.db,
        directories=[Path(d) for d in args.dirs],
        week=args.week,
        from_date=args.from_date,
        to_date=args.to_date,
        max_examples=args.max_examples,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
