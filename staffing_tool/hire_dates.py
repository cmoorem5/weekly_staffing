"""
Bulk date-of-hire import for the staff roster.

Accepts a CSV with a header row. Recognized columns (case and punctuation
ignored):

* ``last name`` + ``first name``, or a single ``name`` column ("Last, First")
* ``role`` (optional; RN / Medic / EMT)
* ``date of hire`` / ``doh`` / ``hire date`` / ``hired``

Dates may be ``YYYY-MM-DD``, ``M/D/YYYY`` or ``M/D/YY``.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy.orm import Session

from .models import StaffRosterEntry

_ROLE_ALIASES = {
    "RN": "RN",
    "NURSE": "RN",
    "MEDIC": "MEDIC",
    "PARAMEDIC": "MEDIC",
    "EMT": "EMT",
}

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d")


@dataclass
class HireDateImportResult:
    updated: int = 0
    unchanged: int = 0
    unmatched: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)


def _norm_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def parse_hire_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    # Spreadsheet exports sometimes append a time component.
    text = text.split(" ")[0] if " " in text and ":" in text else text
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        # Two-digit years: strptime maps 69-99 to 19xx and 00-68 to 20xx.
        return parsed
    return None


def _split_name(value: str) -> tuple[str, str]:
    text = (value or "").strip()
    if "," in text:
        last, first = (p.strip() for p in text.split(",", 1))
        return last, first
    parts = text.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return text, ""


def parse_hire_date_csv(
    text: str,
) -> tuple[list[tuple[str, str, str, date]], list[str]]:
    """Parse CSV text into ``(role, last, first, hire_date)`` rows plus errors."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return [], ["The file is empty."]
    headers = [_norm_header(h) for h in rows[0]]

    def col(*names: str) -> int | None:
        for name in names:
            if name in headers:
                return headers.index(name)
        return None

    i_last = col("lastname", "last", "surname")
    i_first = col("firstname", "first", "givenname")
    i_name = col("name", "employee", "employeename", "fullname")
    i_role = col("role", "position", "title", "track")
    i_doh = col("dateofhire", "doh", "hiredate", "hired", "datehired", "startdate")
    errors: list[str] = []
    if i_doh is None:
        errors.append(
            "No date column found. Use a header such as 'Date of Hire' or 'DOH'."
        )
    if i_last is None and i_name is None:
        errors.append("No name column found. Use 'Last Name' and 'First Name'.")
    if errors:
        return [], errors

    parsed: list[tuple[str, str, str, date]] = []
    for line_no, row in enumerate(rows[1:], start=2):

        def cell(i: int | None, row: list[str] = row) -> str:
            return row[i].strip() if i is not None and i < len(row) else ""

        if i_last is not None:
            last, first = cell(i_last), cell(i_first)
        else:
            last, first = _split_name(cell(i_name))
        if not last:
            errors.append(f"Line {line_no}: no name.")
            continue
        hired = parse_hire_date(cell(i_doh))
        if hired is None:
            errors.append(
                f"Line {line_no}: {last}, {first}".rstrip(", ")
                + f" has an unreadable date '{cell(i_doh)}'."
            )
            continue
        role = _ROLE_ALIASES.get(cell(i_role).upper(), "")
        parsed.append((role, last, first, hired))
    return parsed, errors


def apply_hire_dates(
    session: Session,
    entries: list[tuple[str, str, str, date]],
) -> HireDateImportResult:
    """Write hire dates onto matching roster rows (active or inactive)."""
    result = HireDateImportResult()
    roster = session.query(StaffRosterEntry).all()
    by_last: dict[str, list[StaffRosterEntry]] = {}
    for row in roster:
        by_last.setdefault((row.last_name or "").strip().upper(), []).append(row)

    for role, last, first, hired in entries:
        label = f"{last}, {first}" if first else last
        candidates = by_last.get(last.strip().upper(), [])
        if role:
            candidates = [c for c in candidates if c.role == role]
        if first:
            exact = [
                c
                for c in candidates
                if (c.first_name or "").strip().upper() == first.strip().upper()
            ]
            if exact:
                candidates = exact
            else:
                # Roster row with a blank first name still belongs to this person.
                candidates = [c for c in candidates if not (c.first_name or "").strip()]
        if not candidates:
            result.unmatched.append(label)
            continue
        if len(candidates) > 1:
            result.ambiguous.append(label)
            continue
        target = candidates[0]
        iso = hired.isoformat()
        if target.hire_date == iso:
            result.unchanged += 1
        else:
            target.hire_date = iso
            result.updated += 1
    session.flush()
    return result
