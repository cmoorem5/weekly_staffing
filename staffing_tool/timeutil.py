"""Shared time helpers.

Single source of truth for the UTC timestamp format used across the DB
audit fields (``created_at``/``updated_at``/``imported_at``) so the format
can never drift between modules.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    """Current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
