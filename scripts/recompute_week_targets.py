#!/usr/bin/env python3
"""
Recompute the ground-target and no-vehicle figures for already-imported weeks.

Ground is a system target of 3 day + 1 night truck a day from any base, and a
vehicle marked "no vehicle" never counts against staffing. New imports apply
both automatically; this brings weeks imported earlier in line, using the
name-level OPS View rows already stored (no workbook needed).

Weeks with no name-level rows are left on their current numbers.

Safe to rerun. Use --dry-run first to see how each week's System RW % and
System GR % would change, then run without it (a safety backup of staffing.db
is saved to archive/ before any write).

Usage:
    python scripts/recompute_week_targets.py --dry-run
    python scripts/recompute_week_targets.py
    python scripts/recompute_week_targets.py --from-date 2026-10-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staffing_tool.db import ensure_db_ready, session_scope
from staffing_tool.db_backup import create_db_backup
from staffing_tool.metrics import compute_week_metrics
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyOpsViewAssignment,
    WeeklyStaffing,
)
from staffing_tool.paths import PROJECT_ROOT
from staffing_tool.week_targets import recompute_week_targets


def _pcts(session, week_start: str) -> tuple[float, float]:
    row = session.query(WeeklyStaffing).filter_by(week_start=week_start).one()
    covs = session.query(WeeklyBaseCoverage).filter_by(week_start=week_start).all()
    m = compute_week_metrics(row, covs, session.query(BaseConfig).all())
    return m.system_rw_pct, m.system_gr_pct


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=str(PROJECT_ROOT / "staffing.db"))
    p.add_argument("--from-date", default=None, help="Only weeks on/after YYYY-MM-DD")
    p.add_argument("--to-date", default=None, help="Only weeks on/before YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true", help="Report only; write nothing")
    args = p.parse_args(argv)

    if not Path(args.db).exists():
        p.error(f"database not found: {args.db}")
    ensure_db_ready(args.db)

    if not args.dry_run:
        backup = create_db_backup(args.db)
        print(f"Safety backup: {backup}")

    changed = 0
    with session_scope(args.db) as session:
        weeks = [
            w
            for (w,) in session.query(WeeklyOpsViewAssignment.week_start)
            .group_by(WeeklyOpsViewAssignment.week_start)
            .order_by(WeeklyOpsViewAssignment.week_start)
            .all()
            if (not args.from_date or w >= args.from_date)
            and (not args.to_date or w <= args.to_date)
        ]
        print(
            f"{'Week':<12} {'RW before':>10} {'RW after':>9} {'GR before':>10} {'GR after':>9}"
        )
        for w in weeks:
            rw0, gr0 = _pcts(session, w)
            recompute_week_targets(session, w)
            session.flush()
            rw1, gr1 = _pcts(session, w)
            if (round(rw0, 4), round(gr0, 4)) != (round(rw1, 4), round(gr1, 4)):
                changed += 1
            print(f"{w:<12} {rw0:>10.1%} {rw1:>9.1%} {gr0:>10.1%} {gr1:>9.1%}")
        if args.dry_run:
            session.rollback()
    verb = "would change" if args.dry_run else "changed"
    print(f"\n{len(weeks)} week(s) with detail; {changed} {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
