#!/usr/bin/env python3
"""
Read-only audit of the schedule data the weekly numbers are built from.

Rotor wing and ground coverage count a vehicle as staffed when every required
seat cell holds a name. The importer treats any non-blank cell that is not OPEN
as a name, so a leave code, a placeholder or a stray number in a crew or pilot
cell would count as a person. This lists every staffed cell value that does not
look like a name, with the weeks, vehicles and seats it appears in, so they can
be checked against the workbook.

Writes nothing. For the stored-vs-schedule comparison use
`python scripts/shortfall_causes.py --reconcile`.

Usage:
    python scripts/audit_numbers.py
    python scripts/audit_numbers.py --start 2026-10-01
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from staffing_tool.db import ensure_db_ready, session_scope
from staffing_tool.models import WeeklyOpsViewAssignment, WeeklyStaffing
from staffing_tool.paths import staffing_db_path

# Text that is a code or placeholder rather than a person's name.
_CODE_WORDS = {
    "OPEN", "NONE", "N/A", "NA", "TBD", "TBA", "X", "LT", "AT", "SICK", "PTO",
    "VAC", "LOA", "PFML", "EDU", "OFF", "RTW", "RAL", "JURY", "BREV", "AOC", "SM",
}  # fmt: skip


def looks_like_a_name(value: str) -> bool:
    """False for blanks, digits, short codes and known leave or placeholder words."""
    v = (value or "").strip()
    if not v:
        return False
    if v.upper() in _CODE_WORDS:
        return False
    if re.fullmatch(r"[\d\W_]+", v):
        return False
    if len(v) <= 1:
        return False
    if re.search(r"\d", v):
        return False
    return bool(re.search(r"[A-Za-z]{2}", v))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--db", default=str(staffing_db_path()))
    p.add_argument("--start", default=None, help="Only weeks on/after YYYY-MM-DD")
    args = p.parse_args(argv)
    if not Path(args.db).exists():
        p.error(f"database not found: {args.db}")
    ensure_db_ready(args.db)

    odd: dict[str, dict[str, set | int]] = defaultdict(
        lambda: {"n": 0, "weeks": set(), "where": set()}
    )
    with session_scope(args.db) as session:
        weeks_all = [w for (w,) in session.query(WeeklyStaffing.week_start).all()]
        q = session.query(
            WeeklyOpsViewAssignment.week_start,
            WeeklyOpsViewAssignment.unit_code,
            WeeklyOpsViewAssignment.role,
            WeeklyOpsViewAssignment.raw_value,
        ).filter(WeeklyOpsViewAssignment.is_staffed == 1)
        if args.start:
            q = q.filter(WeeklyOpsViewAssignment.week_start >= args.start)
        rows = q.all()
        with_detail = {r[0] for r in rows} | {
            w for (w,) in session.query(WeeklyOpsViewAssignment.week_start).distinct()
        }
    for week, unit, role, raw in rows:
        if not looks_like_a_name(raw):
            d = odd[(raw or "").strip()]
            d["n"] += 1
            d["weeks"].add(week)
            d["where"].add(f"{unit} {role}")

    print("Staffed schedule cells that do not look like a name")
    print(
        f"Weeks stored: {len(weeks_all)}; with name-level schedule rows: {len(with_detail)}"
    )
    print(f"Staffed cells checked: {len(rows)}\n")
    if not odd:
        print("None found: every staffed cell holds something that looks like a name.")
        return 0
    print(f"{'Cell value':<18} {'Count':>6}  {'Weeks':>5}  Where (vehicle seat)")
    for token, d in sorted(odd.items(), key=lambda kv: -int(kv[1]["n"])):
        where = ", ".join(sorted(d["where"])[:6])
        more = "" if len(d["where"]) <= 6 else f" +{len(d['where']) - 6} more"
        print(f"{token!r:<18} {d['n']:>6}  {len(d['weeks']):>5}  {where}{more}")
    print(
        "\nEach of these counted as a staffed seat. If any is a leave code or a "
        "placeholder, that vehicle-day is overstated: fix it in the schedule or "
        "tell me which codes to treat as not staffed."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
