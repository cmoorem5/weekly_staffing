"""Unit/leave/training/skip code tables and cell classification.

Known-code lists here are additive defaults; Settings-managed TrainingCode
rows and unit-code overrides are merged in at parse time by the callers.
"""

from __future__ import annotations

import re

from .schedule_types import DayNight, ServiceType, SkipReason

# --- Unit mapping -------------------------------------------------------

# Map from canonical unit code (no suffixes like 'c' / 'p') to
# (base_name, service_type, day_night).
#
# Base assignment: Bedford = D7B, N7B, GR, NG; Mansfield = D11M, MG;
# Lawrence = D9L, N9L, LG; Manchester = D11H; Plymouth = D7P, N7P, PG, NP.
UNIT_MAP: dict[str, tuple[str, ServiceType, DayNight]] = {
    # Bedford: D7B, N7B, GR, NG
    "D7B": ("Bedford", "RW", "D"),
    "N7B": ("Bedford", "RW", "N"),
    "GR": ("Bedford", "GR", "D"),
    "NG": ("Bedford", "GR", "N"),
    # Lawrence: D9L, N9L, LG
    "D9L": ("Lawrence", "RW", "D"),
    "N9L": ("Lawrence", "RW", "N"),
    "LG": ("Lawrence", "GR", "D"),
    # Mansfield: D11M, MG
    "D11M": ("Mansfield", "RW", "D"),
    "MG": ("Mansfield", "GR", "D"),
    # Plymouth: D7P, N7P, PG, NP
    "D7P": ("Plymouth", "RW", "D"),
    "N7P": ("Plymouth", "RW", "N"),
    "PG": ("Plymouth", "GR", "D"),
    "NP": ("Plymouth", "GR", "N"),
    # Manchester: D11H
    "D11H": ("Manchester", "RW", "D"),
    # EMT GR shorthand (Bedford ground, aligns with D7B EMT staffing)
    "GR2": ("Bedford", "GR", "D"),
    "NG2": ("Bedford", "GR", "N"),
    # Opportunistic Bedford ground day shift added after FW decommission
    "G9B": ("Bedford", "GR", "D"),
}

# Historical Manchester codes → canonical D11H (same base, RW day as today).
# raw_value keeps the original cell text; unit_code is canonical for aggregates.
LEGACY_UNIT_ALIASES: dict[str, str] = {
    "D9P": "D11H",
    "D9B": "D11H",
    "D11B": "D11H",
}

# One-off data-entry typos for real unit codes, unrelated to the Manchester
# legacy consolidation above.
UNIT_CODE_TYPO_ALIASES: dict[str, str] = {
    "SM/N9LP": "N9L",  # confirmed typo for the Lawrence night RW unit
    "CLNICAL/LG": "LG",  # confirmed typo for a real Lawrence ground shift
}

# Retired units: skip staffed parse; excluded from CEO aggregates.
RETIRED_UNIT_CODES: frozenset[str] = frozenset({"FW"})

# Max RW/GR staffed unit-days per base per week (cap OPS View counts to these).
# Bedford, Plymouth, Lawrence: 14 RW; Bedford: 14 GR;
# Mansfield, Lawrence, Plymouth: 7 GR.
MAX_RW_UNIT_DAYS_PER_WEEK: dict[str, int] = {
    "Bedford": 14,
    "Plymouth": 14,
    "Lawrence": 14,
    "Mansfield": 7,
    "Manchester": 7,
}
MAX_GR_UNIT_DAYS_PER_WEEK: dict[str, int] = {
    "Bedford": 14,
    "Mansfield": 7,
    "Lawrence": 7,
    "Plymouth": 7,
}

# Absence/exception cell values only — not roles (RN/Medic/EMT are row types, not leave codes).
LEAVE_CODES = {"AT", "LT", "SICK", "LOA", "PFML", "JURY", "BREV"}

# Absence cells are written as a family code plus an optional qualifier: an
# hour count (AT8, AT12, LT8), or a separator and a class/detail name
# (AT:FCCS, EDU:STABLE, AT/AIRWAY SIM, SICK SIM, "AT: SIM"). Matching the
# family and ignoring the qualifier is what keeps the next class name off the
# unknown-code review list -- enumerating every qualifier meant a code change
# and a redeploy per variant, and the qualifier never changes the leave type.
# EDU alone is training (SKIP_TRAINING_VALUES), so only a qualified EDU is AT.
_LEAVE_FAMILY_RE = re.compile(
    r"^(?P<family>AT|EDU|LT|SICK)(?P<hours>\d+)?(?P<qualifier>\s*[:/]\s*\S.*|\s+\S.*)?$"
)
_LEAVE_FAMILIES: dict[str, str] = {
    "AT": "AT",
    "EDU": "AT",
    "LT": "LT",
    "SICK": "SICK",
}

# Spellings no family rule covers: military leave, per-diem, and two
# bereavement typos seen in real workbooks.
LEAVE_SPELLING_ALIASES: dict[str, str] = {
    "SM/AT": "AT",
    "M-LT": "LT",
    "MIL (LT)": "LT",
    "LOA (MIL)": "LOA",
    "PER": "LT",
    "SL": "SICK",
    "BRV": "BREV",
    "BERV": "BREV",
}


def classify_leave_value(text: str) -> tuple[str, str]:
    """Map a normalized absence cell to ``(leave_code, display)``.

    ``leave_code`` is what the grid walker checks against LEAVE_CODES;
    ``display`` is what the exception grid shows, so LT-D / LT-N keep their
    day/night direction. An unrecognized value comes back unchanged and falls
    through to unit parsing.
    """
    alias = LEAVE_SPELLING_ALIASES.get(text)
    if alias is not None:
        return alias, alias
    match = _LEAVE_FAMILY_RE.match(text)
    if match is not None:
        family = match["family"]
        if family != "EDU" or match["hours"] or match["qualifier"]:
            code = _LEAVE_FAMILIES[family]
            return code, code
    if text.startswith("LT-"):
        return "LT", text
    return text, text


# Return-to-work and restricted-activity-light markers: "RTW <unit>",
# "RAL <unit>", "RTW ADMIN", "RTW CLINICAL/ADMIN". The person is on the
# schedule but holds no line, whatever follows the prefix, so match the
# prefix rather than listing every unit each has been paired with -- that
# list needed a code change every time a new pairing appeared.
_IGNORED_VALUE_PREFIXES: tuple[str, ...] = ("RTW ", "RAL ")

# Unit-like codes to skip when parsing: no shift record, no unknown-unit
# issue. Spellings the prefix rule above does not cover.
IGNORE_UNIT_CODES: set[str] = {
    "ULTRASOUND",
    "GR-RAL",
    "HOL",
    "PR",
    "ADMIN",
    "SM/RAL LG",
    "CLINICAL/RAL FW",
    "EMT/FW",
    "ZZ",
    "LTM/AOC",
}


def is_ignored_unit_value(text: str) -> bool:
    """True when a cell is a marker to skip, not a unit code to resolve."""
    return text in IGNORE_UNIT_CODES or text.startswith(_IGNORED_VALUE_PREFIXES)


# Training/education markers: not staffing, not leave -- counted separately
# in WeeklyStaffing.training_shifts (weekly total across all these codes).
SKIP_TRAINING_VALUES: set[str] = {
    "SM",
    "SIM",
    "AIRWAY SIM",
    "SM (LIVE)",
    "SM (VIRTUAL)",
    "SM(LIVE)",
    "SM(VIRTUAL)",
    "EDU",
    "CCT",
    "CCTP",
    "NEO SIM",
    "CLINICAL/PER",
    "CLINICAL/ PER",
    "AUDIO",
    "SM/EDU",
    "SM / EDU",
    "SM(LIVE)/AUDIO",
    "SM (LIVE)/AUDIO",
    "SM(VIRTUAL)/AUDIO",
    "SM (VIRTUAL)/AUDIO",
    "SM(LIVE)/ADUIO",  # AUDIO typo seen in real workbooks
    "SM(VIRTUAL)/ADUIO",
}

SKIP_ADMIN_VALUES: set[str] = {
    "AOC",
    "CLINICAL",
    "CINICAL",  # CLINICAL typo seen in real workbooks
    "FLOAT",
    "LTM",
    "MIL",
}

# "CLINICAL/<code>" is a real code that schedulers prefix with "CLINICAL/"
# out of habit; unlike the leave-family qualifiers above, the part after the
# slash IS the actual code (SIM = training, AOC = admin, ADMIN = AT leave),
# not free text. Substitute before classification so every downstream check
# (skip category, manager AOC credit, leave family) sees the same text as
# the un-prefixed form, instead of re-teaching each one a second spelling.
CLINICAL_PREFIX_ALIASES: dict[str, str] = {
    "CLINICAL/SIM": "SIM",
    "CLINICAL/AOC": "AOC",
    "CLINICAL/ADMIN": "AT",
}

# Every cell value the grid walker skips. Derived, never hand-listed: these two
# sets were duplicated into a third literal, so adding a training code meant
# editing two places and forgetting one left the cell parsed as an unknown unit
# instead of skipped.
SKIP_CELL_VALUES: set[str] = SKIP_TRAINING_VALUES | SKIP_ADMIN_VALUES

# Merge rules: apply to ALL unit codes (D7B, N7B, D9L, D11M, D7P, N7P, etc.).
# - OT: trailing "C" or " C" on any known unit → overtime.
#   E.g. D7PC, N7BC, D9LC, D7P C.
# - Leave: "UNIT/LEAVETYPE" → that leave type.
#   E.g. D7B/LT, N7P/SICK, D9L/LOA, D11M/JURY.
OT_SUFFIXES: set[str] = {"C", " C"}
# UNIT/LEAVETYPE patterns: suffix after / maps to leave_type for the grid.
UNIT_LEAVE_MERGE: dict[str, str] = {
    "AT": "AT",
    "SM/AT": "AT",
    "LT": "LT",
    "4LT": "LT",  # e.g. D11M/4LT → LT
    "LT-D": "LT-D",
    "LT-N": "LT-N",
    "SICK": "SICK",
    "LOA": "LOA",
    "PFML": "LOA",
    "JURY": "JURY",
    "BREV": "BREV",
}

# "Little c" variants that mean OT → normalize to C before parsing
# (D7Pᶜ, D7Pç → D7PC)
_OT_C_VARIANTS = ("\u1d9c", "\u0368", "\u00e7", "\u00c7")  # ᶜ, ͨ, ç, Ç


def _normalize_cell_value(raw: object) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        s = raw.strip().upper()
    else:
        s = str(raw).strip().upper()
    for variant in _OT_C_VARIANTS:
        s = s.replace(variant, "C")
    s = s.strip("*").strip()
    return s


def _classify_skip_reason(
    text: str, training_values: frozenset[str] | set[str] = SKIP_TRAINING_VALUES
) -> SkipReason:
    """Map a skipped cell value to a persistence skip_reason.

    training_values: SKIP_TRAINING_VALUES plus any admin-added training
    codes (Settings > Training codes) for this parse call.
    """
    if text == "OPEN":
        return "open"
    if text in training_values:
        return "training"
    # SKIP_ADMIN_VALUES and IGNORE_UNIT_CODES land here, as does anything else
    # the caller decided to skip -- all of it is "admin" for persistence.
    return "admin"


def _canonical_unit_code(code: str) -> str | None:
    """Map legacy aliases to canonical UNIT_MAP keys; None if retired."""
    if code in RETIRED_UNIT_CODES:
        return None
    if code in UNIT_CODE_TYPO_ALIASES:
        return UNIT_CODE_TYPO_ALIASES[code]
    return LEGACY_UNIT_ALIASES.get(code, code)


def _is_resolvable_unit(code: str) -> bool:
    """True when code (or its legacy alias) maps to a known staffed unit."""
    canonical = _canonical_unit_code(code)
    return canonical is not None and canonical in UNIT_MAP


def _split_unit_suffix(code: str) -> tuple[str, bool, bool]:
    """
    Split a unit-like code into (base_unit, is_ot, is_dual_role).

    Strip trailing 'C' (OT) and 'P' (dual-role) only when the remainder is in
    UNIT_MAP (or LEGACY_UNIT_ALIASES), so D7P/N7P (Plymouth) are preserved.
    If core is already resolvable, do not strip. E.g. N7PC -> N7P (strip C);
    D9PC -> D9P (strip C); D7PP -> D7P (strip P); D7P -> D7P (no strip).

    Also supports " C" (space + C) for OT merge: D7P C, D7B C etc. → overtime.
    """
    is_ot = False
    is_dual = False
    core = code

    # Merge: "UNIT C" or "UNIT C" (space + C) → OT when base unit is known
    for suffix in OT_SUFFIXES:
        if core.endswith(suffix):
            candidate = core[: -len(suffix)].strip()
            if _is_resolvable_unit(candidate):
                core = candidate
                is_ot = True
                break

    while len(core) > 1 and core[-1] in {"C", "P"}:
        if _is_resolvable_unit(core):
            break
        last = core[-1]
        candidate = core[:-1]
        if _is_resolvable_unit(candidate):
            core = candidate
            if last == "C":
                is_ot = True
            else:
                is_dual = True
            break
        core = candidate
        if last == "C":
            is_ot = True
        else:
            is_dual = True
    return core, is_ot, is_dual


def _classify_unit(code: str) -> tuple[str, ServiceType, DayNight] | None:
    """Return (base, service_type, day_night) for a cleaned unit, or None."""
    canonical = _canonical_unit_code(code)
    if canonical is None:
        return None
    return UNIT_MAP.get(canonical)
