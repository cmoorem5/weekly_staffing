"""Schedule Exceptions by Role and Type — shared by Excel export and Django UI."""

from __future__ import annotations

EXCEPTION_GRID_COLS: list[str] = ["AT", "LT", "SICK", "LOA", "JURY", "BREV"]

EXCEPTION_GRID_ROLES: list[str] = ["RN", "Medic", "EMT", "Pilot"]

# For each display column, which WeeklyLeaveDetail.leave_type keys roll up into it
EXCEPTION_COL_BREAKDOWN_KEYS: dict[str, list[str]] = {
    "AT": ["AT"],
    "LT": ["LT-D", "LT-N", "LT"],
    "SICK": ["SICK"],
    "LOA": ["LOA", "PFML"],
    "JURY": ["JURY"],
    "BREV": ["BREV"],
}

# Single leave_type stored when saving a column from the web UI
EXCEPTION_COL_DB_TYPE: dict[str, str] = {
    "AT": "AT",
    "LT": "LT",
    "SICK": "SICK",
    "LOA": "LOA",
    "JURY": "JURY",
    "BREV": "BREV",
}

LEAVE_TYPE_TO_FIELD: dict[str, str] = {
    "AT": "leave_at",
    "LT": "leave_lt",
    "SICK": "leave_sick",
    "LOA": "leave_loa",
    "JURY": "leave_jury",
    "BREV": "leave_brev",
}

# Column order matching EXCEPTION_GRID_COLS — used for Excel rollups
EXCEPTION_COL_KEYS: list[list[str]] = [
    EXCEPTION_COL_BREAKDOWN_KEYS[c] for c in EXCEPTION_GRID_COLS
]
