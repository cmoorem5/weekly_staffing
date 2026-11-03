#!/usr/bin/env python3
"""
Why were planned vehicles not staffed: no crew, or no operator?

Read-only. For each planned rotor-wing (RW) and ground (GR) vehicle-day in the
window, says whether a short vehicle was missing crew (RN and/or Medic), its
operator (Pilot on RW, EMT on GR), or both. Built from the OPS View
assignments already stored at import, so no re-import is needed.

Prints a summary by vehicle type, base, and week, and writes two CSVs:
  output/shortfall_detail_<start>_<end>.csv   one row per short vehicle-day
  output/shortfall_summary_<start>_<end>.csv  counts by week, base, and cause

Usage:
    python scripts/shortfall_causes.py                      # Oct 1 to today
    python scripts/shortfall_causes.py --start 2026-10-01 --end 2026-11-02
    python scripts/shortfall_causes.py --db path/to/staffing.db
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
from staffing_tool.paths import PROJECT_ROOT
from staffing_tool.shortfall import (
    CAUSE_BOTH,
    CAUSE_CREW,
    CAUSE_OPERATOR,
    CAUSES,
    ShortfallResult,
    load_shortfalls,
    operator_label,
    summarize_by,
)

_SERVICE_NAMES = {"RW": "Rotor wing", "GR": "Ground"}


def _default_start(today: date) -> date:
    """October 1 of the current fiscal year."""
    year = today.year if today.month >= 10 else today.year - 1
    return date(year, 10, 1)


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
        planned = sum(n for (b, s), n in result.planned.items() if s == service)
        staffed = sum(n for (b, s), n in result.staffed.items() if s == service)
        shorts = [s for s in result.shortfalls if s.service_type == service]
        by_cause = {c: sum(1 for s in shorts if s.cause == c) for c in CAUSES}
        print(
            f"== {_SERVICE_NAMES[service]} ({operator_label(service)} is the operator) =="
        )
        print(
            f"Planned vehicle-days {planned}, staffed {staffed} "
            f"({_pct(staffed, planned)}), short {len(shorts)}"
        )
        print(
            f"  Short because: no crew {by_cause[CAUSE_CREW]}, "
            f"no {operator_label(service)} {by_cause[CAUSE_OPERATOR]}, "
            f"both {by_cause[CAUSE_BOTH]}"
        )
        print()

        base_rows = []
        by_base = summarize_by(_filter(result, service), key=lambda s: s.base)
        bases = sorted({b for (b, s) in result.planned if s == service}, key=_base_rank)
        for base in bases:
            p = result.planned[(base, service)]
            st = result.staffed[(base, service)]
            c = by_base.get(base, dict.fromkeys(CAUSES, 0))
            base_rows.append(
                [
                    base,
                    p,
                    st,
                    _pct(st, p),
                    c[CAUSE_CREW],
                    c[CAUSE_OPERATOR],
                    c[CAUSE_BOTH],
                ]
            )
        _print_table(
            ["Base", "Planned", "Staffed", "%"] + _cause_headers(service),
            base_rows,
        )
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
            ]
        )
        for s in sorted(
            result.shortfalls, key=lambda s: (s.day, _base_rank(s.base), s.unit_code)
        ):
            w.writerow(
                [
                    s.day.isoformat(),
                    _sunday_on_or_before(s.day).isoformat(),
                    s.base,
                    s.service_type,
                    s.unit_code,
                    s.day_night,
                    s.cause,
                    s.detail,
                ]
            )

    counts: dict[tuple, int] = defaultdict(int)
    for s in result.shortfalls:
        counts[(_sunday_on_or_before(s.day), s.base, s.service_type, s.cause)] += 1
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=str(PROJECT_ROOT / "staffing.db"))
    p.add_argument("--start", type=date.fromisoformat, default=None)
    p.add_argument("--end", type=date.fromisoformat, default=None)
    p.add_argument("--out-dir", default=str(PROJECT_ROOT / "output"))
    args = p.parse_args(argv)

    today = date.today()
    start = args.start or _default_start(today)
    end = args.end or today
    if end < start:
        p.error("--end is before --start")
    if not Path(args.db).exists():
        p.error(f"database not found: {args.db}")

    ensure_db_ready(args.db)
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
