"""
Admin-managed training/education codes for schedule import.

Rows are stored in staffing.db (training_code table) and edited via Settings
> Training codes. Additive on top of schedule_import.SKIP_TRAINING_VALUES
(defined in schedule_cells.py) --
this is for a new code (class, sim type, etc.) staff start using that the
built-in list doesn't know about yet, not a replacement for it.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import TrainingCode


def training_codes_upper_from_session(session: Session) -> frozenset[str]:
    """Admin-added training codes from DB (may be empty)."""
    rows = session.query(TrainingCode.code).all()
    return frozenset(r[0].strip().upper() for r in rows if r[0] and str(r[0]).strip())
