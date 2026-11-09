#!/usr/bin/env python3
"""
Why were planned vehicles not staffed: no crew, or no operator?

Read-only. For each planned rotor-wing (RW) and ground (GR) vehicle-day in the
window, says whether a short vehicle was missing crew (RN and/or Medic), its
operator (Pilot on RW, EMT on GR), or both. Built from the OPS View
assignments already stored at import, so no re-import is needed.

Rotor wing is scored per base. Ground is scored against the system target of
4 trucks a day, 3 on days and 1 on nights, from any base (crews move to where
the staffing is), so ground is reported by shift, not by base. Vehicles marked
out of service on the week edit page ("No vehicle") are listed in their own
section and never counted against staffing.

Prints a summary by vehicle type, base, and week, and writes two CSVs:
  output/shortfall_detail_<start>_<end>.csv   one row per short or no-vehicle day
  output/shortfall_summary_<start>_<end>.csv  counts by week, base, and cause

Usage:
    python scripts/shortfall_causes.py                      # Oct 1 to today
    python scripts/shortfall_causes.py --start 2026-10-01 --end 2026-11-02
    python scripts/shortfall_causes.py --db path/to/staffing.db
    python scripts/shortfall_causes.py --reconcile          # where stored != schedule
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staffing_tool.db import ensure_db_ready
from staffing_tool.metrics import BASE_DISPLAY_ORDER
from staffing_tool.paths import PROJECT_ROOT, staffing_db_path
from staffing_tool.schedule_import import UNIT_MAP
from staffing_tool.shortfall import (
    CAUSE_BOTH,
    CAUSE_CREW,
    CAUSE_OPERATOR,
    CAUSES,
    GROUND_DAY_PER_DAY,
    GROUND_DAY_SCOPE,
    GROUND_NIGHT_PER_DAY,
    GROUND_NIGHT_SCOPE,
    REASON_TABLE_HEADERS,
    SYSTEM_GROUND_PER_DAY,
    ShortfallResult,
    load_shortfalls,
    operator_label,
    reason_table_rows,
    reconcile_week,
    summarize_by,
)

_SERVICE_NAMES = {"RW": "Rotor wing", "GR": "Ground"}


def _default_start(today: date) -> date:
    """October 1 of the current fiscal year."""
    year = today.year if today.month >= 10 else today.year - 1
    return date(year, 10, 1)


def _base_label(service_type: str, base: str) -> str:
    """Ground comes from any base, so its blamed truck's base is not meaningful."""
    return "Any" if service_type == "GR" else base


def _sunday_on_or_before(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def _pct(num: int, den: int) -> str:
    return f"{num / den * 100:.1f}%" if den else "n/a"


def _base_rank(base: str) -> int:
    try:
        return list(BASE_DISPLAY_ORDER).index(base)
    except ValueError:
        return len(BASE_DISPLAY_ORDER)


def _print_table(headers: list[str], rows: list[list[object]]) -> None:
    widths = [
        max(len(str(h)), *(len(str(r[i])) for r in rows)) if rows else len(str(h))
        for i, h in enumerate(headers)
    ]
    print("  ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers)))
    for r in rows:
        print("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))


def _cause_headers(service: str | None) -> list[str]:
    op = operator_label(service) if service else "operator"
    return ["No crew", f"No {op}", "Both"]


def report(result: ShortfallResult, start: date, end: date) -> None:
    print(f"Staffing shortfall causes, {start} to {end}")
    print(f"Days analyzed: {result.days_analyzed}")
    if result.weeks_without_detail:
        print(
            "Weeks with no name-level OPS View data (left out, not counted as "
            f"short): {', '.join(result.weeks_without_detail)}"
        )
    print()

    for service in ("RW", "GR"):
        planned = sum(n for (_scope, s), n in result.planned.items() if s == service)
        if service == "GR":
            # Weekly ground = every ground shift staffed (an extra truck one day
            # offsets a short day), capped at the plan, as on the dashboard.
            staffed = min(
                sum(n for (_scope, s), n in result.staffed_all.items() if s == "GR"),
                planned,
            )
        else:
            staffed = sum(
                n for (_scope, s), n in result.staffed.items() if s == service
            )
        shorts = [s for s in result.shortfalls if s.service_type == service]
        by_cause = {c: sum(1 for s in shorts if s.cause == c) for c in CAUSES}
        op = operator_label(service)
        print(f"== {_SERVICE_NAMES[service]} ({op} is the operator) ==")
        if service == "GR":
            print(
                f"Target {SYSTEM_GROUND_PER_DAY} trucks a day for the system: "
                f"{GROUND_DAY_PER_DAY} day + {GROUND_NIGHT_PER_DAY} night, any base"
            )
        print(
            f"Planned vehicle-days {planned}, staffed {staffed} "
            f"({_pct(staffed, planned)}), short {len(shorts)}"
        )
        print(
            f"  Short because: no crew {by_cause[CAUSE_CREW]}, "
            f"no {op} {by_cause[CAUSE_OPERATOR]}, "
            f"both {by_cause[CAUSE_BOTH]}"
        )
        print()

        by_base = summarize_by(_filter(result, service), key=lambda s: s.base)
        if service == "RW":
            bases = sorted(
                {b for (b, s) in result.planned if s == "RW"}, key=_base_rank
            )
            base_rows = []
            for base in bases:
                p = result.planned[(base, "RW")]
                st = result.staffed[(base, "RW")]
                c = by_base.get(base, dict.fromkeys(CAUSES, 0))
                base_rows.append(
                    [base, p, st, _pct(st, p)]
                    + [c[CAUSE_CREW], c[CAUSE_OPERATOR], c[CAUSE_BOTH]]
                )
            _print_table(
                ["Base", "Planned", "Staffed", "%"] + _cause_headers(service),
                base_rows,
            )
        else:
            by_shift = summarize_by(_filter(result, service), key=lambda s: s.day_night)
            shift_rows = []
            for label, scope, shift in (
                (f"Day ({GROUND_DAY_PER_DAY} a day)", GROUND_DAY_SCOPE, "D"),
                (f"Night ({GROUND_NIGHT_PER_DAY} a day)", GROUND_NIGHT_SCOPE, "N"),
            ):
                p = result.planned[(scope, "GR")]
                st = result.staffed_all[(scope, "GR")]
                c = by_shift.get(shift, dict.fromkeys(CAUSES, 0))
                shift_rows.append(
                    [label, p, st] + [c[CAUSE_CREW], c[CAUSE_OPERATOR], c[CAUSE_BOTH]]
                )
            _print_table(
                ["Shift", "Planned", "Staffed (all trucks)"] + _cause_headers(service),
                shift_rows,
            )
            print(
                "Short counts are the days a seat was missing; the weekly % above "
                "adds up every ground shift staffed."
            )
        print()

    seat_rows = reason_table_rows(result)
    if seat_rows:
        print("== Short by seat (vehicle-days missing this seat) ==")
        _print_table(REASON_TABLE_HEADERS, seat_rows)
        print("A vehicle missing two seats is counted under each.")
        print()

    print("== By week (Sunday start) ==")
    by_week = summarize_by(
        result,
        key=lambda s: (_sunday_on_or_before(s.day), s.service_type),
    )
    weeks = sorted({w for (w, _s) in by_week})
    week_rows = []
    for w in weeks:
        row: list[object] = [w.isoformat()]
        for service in ("RW", "GR"):
            c = by_week.get((w, service), dict.fromkeys(CAUSES, 0))
            row += [c[CAUSE_CREW], c[CAUSE_OPERATOR], c[CAUSE_BOTH]]
        week_rows.append(row)
    _print_table(
        ["Week", "RW crew", "RW pilot", "RW both", "GR crew", "GR EMT", "GR both"],
        week_rows,
    )

    print()
    print("== No vehicle (out of service; never counted against staffing) ==")
    if not result.no_vehicle:
        print("None recorded.")
        return
    staffed_n = sum(1 for n in result.no_vehicle if n.crew_present)
    print(
        f"{len(result.no_vehicle)} vehicle-day(s): {staffed_n} with a full crew on "
        f"shift (counted as staffed), {len(result.no_vehicle) - staffed_n} without "
        "(removed from planned)"
    )
    _print_table(
        ["Date", "Base", "Type", "Unit", "Treatment", "Note"],
        [
            [
                n.day.isoformat(),
                n.base,
                n.service_type,
                n.unit_code,
                n.treatment,
                n.note,
            ]
            for n in sorted(
                result.no_vehicle,
                key=lambda n: (n.day, _base_rank(n.base), n.unit_code),
            )
        ],
    )


def _filter(result: ShortfallResult, service: str) -> ShortfallResult:
    out = ShortfallResult()
    out.shortfalls = [s for s in result.shortfalls if s.service_type == service]
    return out


def write_csvs(
    result: ShortfallResult, start: date, end: date, out_dir: Path
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    detail = out_dir / f"shortfall_detail_{start}_{end}.csv"
    summary = out_dir / f"shortfall_summary_{start}_{end}.csv"

    with detail.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "date",
                "week_start",
                "base",
                "vehicle_type",
                "unit",
                "shift",
                "cause",
                "missing",
                "counts_against_staffing",
                "note",
            ]
        )
        for s in sorted(
            result.shortfalls, key=lambda s: (s.day, _base_rank(s.base), s.unit_code)
        ):
            w.writerow(
                [
                    s.day.isoformat(),
                    _sunday_on_or_before(s.day).isoformat(),
                    _base_label(s.service_type, s.base),
                    s.service_type,
                    s.unit_code,
                    s.day_night,
                    s.cause,
                    s.detail,
                    "Y",
                    "",
                ]
            )
        for n in sorted(
            result.no_vehicle, key=lambda n: (n.day, _base_rank(n.base), n.unit_code)
        ):
            w.writerow(
                [
                    n.day.isoformat(),
                    _sunday_on_or_before(n.day).isoformat(),
                    n.base,
                    n.service_type,
                    n.unit_code,
                    UNIT_MAP[n.unit_code][2],
                    "No vehicle",
                    n.treatment,
                    "N",
                    n.note,
                ]
            )

    counts: dict[tuple, int] = defaultdict(int)
    for s in result.shortfalls:
        counts[
            (
                _sunday_on_or_before(s.day),
                _base_label(s.service_type, s.base),
                s.service_type,
                s.cause,
            )
        ] += 1
    with summary.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["week_start", "base", "vehicle_type", "cause", "short_vehicle_days"]
        )
        for (wk, base, svc, cause), n in sorted(
            counts.items(),
            key=lambda kv: (kv[0][0], _base_rank(kv[0][1]), kv[0][2], kv[0][3]),
        ):
            w.writerow([wk.isoformat(), base, svc, cause, n])
    return [detail, summary]


def reconcile(db_path: str, start: date, end: date) -> None:
    """List where stored coverage differs from the saved schedule detail."""
    from datetime import timedelta

    from staffing_tool.db import session_scope
    from staffing_tool.models import WeeklyOpsViewAssignment

    floor = (start - timedelta(days=6)).isoformat()
    with session_scope(db_path) as session:
        weeks = [
            w
            for (w,) in session.query(WeeklyOpsViewAssignment.week_start)
            .filter(WeeklyOpsViewAssignment.week_start >= floor)
            .filter(WeeklyOpsViewAssignment.week_start <= end.isoformat())
            .distinct()
            .order_by(WeeklyOpsViewAssignment.week_start)
            .all()
        ]
        rows = [r for w in weeks for r in reconcile_week(session, w)]
    differing = [r for r in rows if r.diff]
    print(f"Stored coverage vs saved schedule detail, {start} to {end}")
    print(
        "Stored = what the dashboard and reports use (includes any hand corrections "
        "on the week edit page).\nFrom schedule = what the saved schedule rows imply. "
        "A difference means a hand correction\nor schedule detail that is out of date; "
        "the reasons are built from the schedule only.\n"
    )
    if not differing:
        print(f"All {len(rows)} comparisons match.")
        return
    _print_table(
        ["Week", "Scope", "Stored", "From schedule", "Difference"],
        [
            [r.week_start, r.scope, r.stored, r.from_schedule, f"{r.diff:+d}"]
            for r in differing
        ],
    )
    print(f"\n{len(differing)} of {len(rows)} comparisons differ.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=str(staffing_db_path()))
    p.add_argument("--start", type=date.fromisoformat, default=None)
    p.add_argument("--end", type=date.fromisoformat, default=None)
    p.add_argument("--out-dir", default=str(PROJECT_ROOT / "output"))
    p.add_argument(
        "--reconcile",
        action="store_true",
        help="List weeks where stored coverage differs from the saved schedule detail",
    )
    args = p.parse_args(argv)

    today = date.today()
    start = args.start or _default_start(today)
    end = args.end or today
    if end < start:
        p.error("--end is before --start")
    if not Path(args.db).exists():
        p.error(f"database not found: {args.db}")

    ensure_db_ready(args.db)
    if args.reconcile:
        reconcile(args.db, start, end)
        return 0
    result = load_shortfalls(args.db, start, end)
    if not result.days_analyzed:
        print("No weeks with name-level OPS View data in that range.")
        return 1
    report(result, start, end)
    for path in write_csvs(result, start, end, Path(args.out_dir)):
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
