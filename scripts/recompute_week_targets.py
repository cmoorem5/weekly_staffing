#!/usr/bin/env python3
"""
Bring already-imported weeks in line with the current number definitions.

- **Staffing rate** is vehicles fully staffed (crew plus pilot or EMT), capped at
  the plan, not RN + Medic crews. It is worked out from each week's base
  coverage (including any corrections made on the week edit page).
- **Ground** is a system target of 3 day + 1 night truck a day from any base.
- **No vehicle** marks never count against staffing.
- **Pilot vacancies** (rotor wing days with no pilot) and **overnights below
  coverage** (nights with a planned night vehicle unstaffed) are calculated from
  the saved schedule, net of days fixed by hand.

New imports and saves apply all of this automatically; this brings the weeks
imported earlier in line. Every week with base coverage is refreshed; pilot
vacancies and overnights need the saved schedule rows, so a week without them
keeps its current values.

Safe to rerun. Use --dry-run first to see each week before and after (nothing is
written); a safety backup of staffing.db is saved to archive/ before any write.

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
from staffing_tool.metrics import REQUIRED_TOTAL, compute_week_metrics
from staffing_tool.models import BaseConfig, WeeklyBaseCoverage, WeeklyStaffing
from staffing_tool.paths import PROJECT_ROOT
from staffing_tool.week_targets import recompute_week_targets


def _snapshot(session, week_start: str) -> dict[str, float]:
    row = session.query(WeeklyStaffing).filter_by(week_start=week_start).one()
    covs = session.query(WeeklyBaseCoverage).filter_by(week_start=week_start).all()
    m = compute_week_metrics(row, covs, session.query(BaseConfig).all())
    return {
        "old_rate": (int(row.filled_day or 0) + int(row.filled_night or 0))
        / REQUIRED_TOTAL,
        "rate": m.staffing_rate,
        "rw": m.system_rw_pct,
        "gr": m.system_gr_pct,
        "pilot": int(row.pilot_vacancies or 0),
        "nights": int(row.overnights_below or 0),
    }


def _pct(v: float) -> str:
    return f"{v:.1%}"


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
            for (w,) in session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start)
            .all()
            if (not args.from_date or w >= args.from_date)
            and (not args.to_date or w <= args.to_date)
        ]
        print(
            f"{'Week':<12} {'Staffing old':>12} {'new':>7} {'RW before':>10} "
            f"{'after':>7} {'GR before':>10} {'after':>7} {'Pilot vac':>10} "
            f"{'Overnights':>11}"
        )
        for w in weeks:
            before = _snapshot(session, w)
            recompute_week_targets(session, w)
            session.flush()
            after = _snapshot(session, w)
            if any(
                round(before[k], 4) != round(after[k], 4)
                for k in ("rw", "gr", "pilot", "nights")
            ) or round(before["old_rate"], 4) != round(after["rate"], 4):
                changed += 1
            print(
                f"{w:<12} {_pct(before['old_rate']):>12} {_pct(after['rate']):>7} "
                f"{_pct(before['rw']):>10} {_pct(after['rw']):>7} "
                f"{_pct(before['gr']):>10} {_pct(after['gr']):>7} "
                f"{before['pilot']:>4} -> {after['pilot']:<3} "
                f"{before['nights']:>4} -> {after['nights']:<3}"
            )
        if args.dry_run:
            session.rollback()
    verb = "would change" if args.dry_run else "changed"
    print(f"\n{len(weeks)} week(s); {changed} {verb}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
