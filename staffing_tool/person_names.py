"""
Parse schedule grid name cells (columns A–B) into clean person labels.

RN/Medic: column A is a shift letter (P, n, D, PD, m); column B is last name.
EMT: paired rows may list two people across A and B (group prefixes like ``1-``).
"""

from __future__ import annotations

import re

# Column A on RN & Medic rows — not part of the person's name.
RN_MEDIC_SHIFT_LETTERS: frozenset[str] = frozenset({"P", "n", "D", "PD", "m"})

# Section labels and non-person cells (uppercase match).
SKIP_NAME_VALUES: frozenset[str] = frozenset(
    {
        "LINK TO OPS VIEW",
        "OPEN",
        "EXTRA",
        "ORIENTEE",
        "TRAINING",
    }
)

_ROW_GROUP_PREFIX_RE = re.compile(r"^\d+-")

# Legacy garbled labels: ``D, Cowart``, ``1-Chatigny, Aaron, Chatigny``
_LEGACY_SHIFT_PREFIX_RE = re.compile(
    r"^(?:[Pp]|[Nn]|[Dd]|PD|[Mm]),\s+"
)
_LEGACY_ROW_GROUP_RE = re.compile(r"^\d+-")


def _preserve_name_part(word: str) -> str:
    """Keep Excel casing when mixed; otherwise title-case."""
    if not word:
        return ""
    if "-" in word:
        return "-".join(_preserve_name_part(p) for p in word.split("-"))
    if any(c.isupper() for c in word[1:]):
        return word
    return word[:1].upper() + word[1:].lower()


def is_rn_medic_shift_letter(value: str) -> bool:
    return (value or "").strip() in RN_MEDIC_SHIFT_LETTERS


def parse_name_cell(raw: object, *, is_col_a: bool = True) -> str | None:
    """
    Parse one grid cell into ``Last, First`` or ``Last`` when first is unknown.

    Strips EMT row-group prefixes (``1-``, ``2-``, …) from column A.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.upper() in SKIP_NAME_VALUES:
        return None
    if is_col_a and _ROW_GROUP_PREFIX_RE.match(s):
        s = _ROW_GROUP_PREFIX_RE.sub("", s, count=1).strip()
        if not s:
            return None
    if is_rn_medic_shift_letter(s):
        return None

    if "," in s:
        last, first_part = (p.strip() for p in s.split(",", 1))
        first = first_part.split("/")[0].strip() if first_part else ""
        if not last:
            return None
        if first:
            return f"{_preserve_name_part(last)}, {_preserve_name_part(first)}"
        return _preserve_name_part(last)

    tokens = s.split()
    if len(tokens) == 1:
        return _preserve_name_part(tokens[0])

    first = " ".join(tokens[:-1])
    last = tokens[-1]
    return f"{_preserve_name_part(last)}, {_preserve_name_part(first)}"


def dedupe_person_names(names: list[str]) -> list[str]:
    """Drop duplicate last names; prefer ``Last, First`` over last-only."""
    if not names:
        return []

    def _last_key(label: str) -> str:
        return label.split(",", 1)[0].strip().lower()

    ranked = sorted(
        names,
        key=lambda n: ("," in n, len(n)),
        reverse=True,
    )
    kept: list[str] = []
    seen_last: set[str] = set()
    for name in ranked:
        lk = _last_key(name)
        if lk in seen_last:
            continue
        seen_last.add(lk)
        kept.append(name)
    return sorted(kept, key=lambda n: n.lower())


def rn_medic_person_displays(raw_a: str, raw_b: str) -> list[str]:
    """RN/Medic: shift letter in A, last name in B."""
    b = (raw_b or "").strip()
    if not b or b.upper() in SKIP_NAME_VALUES:
        return []
    a = (raw_a or "").strip()
    if a and is_rn_medic_shift_letter(a):
        return [b.strip()]
    # Unexpected layout — parse both cells if they look like names.
    names: list[str] = []
    for raw, is_a in ((raw_a, True), (raw_b, False)):
        parsed = parse_name_cell(raw, is_col_a=is_a)
        if parsed:
            names.append(parsed)
    return dedupe_person_names(names)


def emt_person_displays(raw_a: str, raw_b: str) -> list[str]:
    """EMT: up to two people per row (columns A and B)."""
    names: list[str] = []
    for raw, is_a in ((raw_a, True), (raw_b, False)):
        parsed = parse_name_cell(raw, is_col_a=is_a)
        if parsed:
            names.append(parsed)
    return dedupe_person_names(names)


def person_displays_for_role(
    role: str,
    raw_a: str,
    raw_b: str,
) -> tuple[str, ...]:
    if role in {"RN", "MEDIC"}:
        return tuple(rn_medic_person_displays(raw_a, raw_b))
    if role == "EMT":
        return tuple(emt_person_displays(raw_a, raw_b))
    return ()


def person_sort_key(name: str) -> tuple[str, str]:
    """Sort key for dropdowns: last name, then first."""
    if "," in name:
        last, first = name.split(",", 1)
        return (last.strip().lower(), first.strip().lower())
    return (name.strip().lower(), "")


def is_plausible_person_display(name: str) -> bool:
    """Filter obvious junk from legacy imports and section labels."""
    s = (name or "").strip()
    if not s:
        return False
    if s.upper() in SKIP_NAME_VALUES:
        return False
    if _LEGACY_ROW_GROUP_RE.match(s):
        return False
    if _LEGACY_SHIFT_PREFIX_RE.match(s):
        return False
    # ``1-Chatigny, Aaron, Chatigny`` — multiple people concatenated.
    if s.count(",") >= 2:
        return False
    before_comma = s.split(",", 1)[0].strip()
    if is_rn_medic_shift_letter(before_comma):
        return False
    return True


_SHIFT_SUFFIX_TOKEN_RE = re.compile(r"^[A-Za-z]\.$")
_NAME_WITH_SHIFT_SUFFIX_RE = re.compile(r"^.+\s+[A-Za-z]\.$")


def _is_shift_suffix_token(token: str) -> bool:
    """Single letter + period (e.g. ``K.`` on ``Phillips K.`` RN/Medic rows)."""
    t = (token or "").strip()
    if not t:
        return False
    if _SHIFT_SUFFIX_TOKEN_RE.match(t):
        return True
    return is_rn_medic_shift_letter(t.rstrip("."))


def is_likely_person_name(name: str) -> bool:
    """
    Stricter person filter for roster import suggestions and auto-add.

    Rejects section labels, orientee rows, and last-name + shift-letter cells
    (``Phillips K.``, ``K., Phillips``) that are not real people.
    """
    s = (name or "").strip()
    if not s:
        return False
    if not is_plausible_person_display(s):
        return False
    if "orientee" in s.lower():
        return False
    if _NAME_WITH_SHIFT_SUFFIX_RE.match(s):
        return False
    if "," in s:
        last, first = (p.strip() for p in s.split(",", 1))
        if _is_shift_suffix_token(last):
            return False
        if first.upper() in SKIP_NAME_VALUES:
            return False
    else:
        if s.upper() in SKIP_NAME_VALUES:
            return False
    return True


def normalize_legacy_person_display(name: str) -> str | None:
    """
  Attempt to recover a clean label from a legacy garbled ``person_display``.

  Returns None when the value cannot be normalized confidently.
  """
    s = (name or "").strip()
    if not s:
        return None
    if is_plausible_person_display(s):
        return s

    if _LEGACY_SHIFT_PREFIX_RE.match(s):
        rest = _LEGACY_SHIFT_PREFIX_RE.sub("", s, count=1).strip()
        parsed = parse_name_cell(rest, is_col_a=False)
        if parsed and is_plausible_person_display(parsed):
            return parsed
        if rest and is_plausible_person_display(rest):
            return _preserve_name_part(rest) if "," not in rest else rest

    if _LEGACY_ROW_GROUP_RE.match(s):
        rest = _LEGACY_ROW_GROUP_RE.sub("", s, count=1).strip()
        parts = [p.strip() for p in rest.split(",") if p.strip()]
        if len(parts) >= 2:
            candidate = f"{_preserve_name_part(parts[0])}, {_preserve_name_part(parts[1])}"
            if is_plausible_person_display(candidate):
                return candidate
        parsed = parse_name_cell(rest, is_col_a=True)
        if parsed and is_plausible_person_display(parsed):
            return parsed

    return None
