"""
Load and persist unit code overrides for schedule import.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from .models import UnitCodeMapping
from .paths import PROJECT_ROOT

DEFAULT_UNIT_MAP_CSV = PROJECT_ROOT / "data" / "unit_mappings.csv"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_key(raw: str) -> str:
    return raw.strip().upper()


def _normalize_value(maps_to: str) -> str:
    return maps_to.strip().upper()


def load_unit_mappings_from_csv(path: str | Path) -> dict[str, str]:
    """Read raw,maps_to rows from a CSV file."""
    result: dict[str, str] = {}
    csv_path = Path(path)
    if not csv_path.is_file():
        return result
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return result
        fields = {f.lower().strip(): f for f in reader.fieldnames if f}
        raw_col = fields.get("raw") or fields.get("raw_code")
        map_col = (
            fields.get("maps_to") or fields.get("map_to") or fields.get("canonical")
        )
        if not raw_col or not map_col:
            return result
        for row in reader:
            raw = (row.get(raw_col) or "").strip()
            maps_to = (row.get(map_col) or "").strip()
            if raw and maps_to:
                result[_normalize_key(raw)] = _normalize_value(maps_to)
    return result


def load_unit_mappings_from_db(session: Session) -> dict[str, str]:
    """All persisted mappings from unit_code_mappings."""
    rows = session.query(UnitCodeMapping).all()
    return {_normalize_key(r.raw_code): _normalize_value(r.maps_to) for r in rows}


def save_unit_mappings(
    session: Session,
    mappings: dict[str, str],
    *,
    source: str = "dashboard",
) -> int:
    """
    Upsert raw -> maps_to pairs. Returns count of rows created or updated.
    """
    if not mappings:
        return 0
    now = _utc_now_iso()
    changed = 0
    for raw, maps_to in mappings.items():
        key = _normalize_key(raw)
        value = _normalize_value(maps_to)
        if not key or not value:
            continue
        row = (
            session.query(UnitCodeMapping)
            .filter(UnitCodeMapping.raw_code == key)
            .first()
        )
        if row:
            if row.maps_to != value or row.source != source:
                row.maps_to = value
                row.source = source[:32]
                row.updated_at = now
                changed += 1
        else:
            session.add(
                UnitCodeMapping(
                    raw_code=key,
                    maps_to=value,
                    source=source[:32],
                    created_at=now,
                    updated_at=now,
                )
            )
            changed += 1
    if changed:
        session.flush()
    return changed


def merge_unit_mappings(*sources: dict[str, str] | None) -> dict[str, str]:
    """Later dicts override earlier keys."""
    merged: dict[str, str] = {}
    for source in sources:
        if source:
            merged.update(source)
    return merged


def resolve_unit_overrides(
    session: Session,
    *,
    extra_csv_paths: list[str | Path] | None = None,
) -> dict[str, str]:
    """
    DB mappings, then data/unit_mappings.csv if present, then optional CSV paths.
    """
    paths: list[Path] = []
    if DEFAULT_UNIT_MAP_CSV.is_file():
        paths.append(DEFAULT_UNIT_MAP_CSV)
    if extra_csv_paths:
        paths.extend(Path(p) for p in extra_csv_paths)

    csv_maps: dict[str, str] = {}
    for path in paths:
        csv_maps = merge_unit_mappings(csv_maps, load_unit_mappings_from_csv(path))

    return merge_unit_mappings(load_unit_mappings_from_db(session), csv_maps)
