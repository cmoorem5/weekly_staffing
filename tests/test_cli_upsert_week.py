"""upsert-week refuses OT input that the reports would ignore or can't split.

compute_week_metrics and role_ot_totals read the day/night OT split first and
only fall back to the per-role columns when the split is empty, so --ot-* on
an imported week used to be stored and never reported; a bare --ot-shifts
reported as 0 OT for every role.
"""

import argparse
import unittest

from staffing_tool.cli import _cmd_upsert_week
from staffing_tool.db import session_scope
from staffing_tool.metrics import compute_week_metrics, role_ot_totals
from staffing_tool.models import WeeklyStaffing
from tests._temp_db import TempDbTestCase

WEEK = "2026-05-10"


def _args(**kw):
    base = {"week_start": WEEK, "entered_by": "test"}
    base.update(kw)
    return argparse.Namespace(**base)


class UpsertWeekOtTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()

    def _row(self):
        with session_scope(self.db_path) as session:
            row = session.query(WeeklyStaffing).filter_by(week_start=WEEK).one()
            session.expunge(row)
            return row

    def test_new_week_with_role_split_syncs_total(self):
        _cmd_upsert_week(
            _args(filled_day=56, filled_night=28, ot_rn=3, ot_medic=2, ot_emt=1),
            self.db_path,
        )
        row = self._row()
        self.assertEqual(row.ot_shifts, 6)
        self.assertEqual(role_ot_totals(row), {"RN": 3, "MEDIC": 2, "EMT": 1})

    def test_new_week_bare_ot_shifts_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "role split"):
            _cmd_upsert_week(
                _args(filled_day=56, filled_night=28, ot_shifts=5), self.db_path
            )
        with session_scope(self.db_path) as session:
            self.assertEqual(session.query(WeeklyStaffing).count(), 0)

    def test_ot_shifts_must_match_role_total(self):
        with self.assertRaisesRegex(ValueError, "doesn't match"):
            _cmd_upsert_week(
                _args(filled_day=56, filled_night=28, ot_shifts=9, ot_rn=3),
                self.db_path,
            )

    def test_imported_week_rejects_ot_flags_and_keeps_data(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK,
                    filled_day=56,
                    filled_night=28,
                    ot_rn_day=2,
                    ot_rn=2,
                    ot_shifts=2,
                )
            )
            session.commit()
        with self.assertRaisesRegex(ValueError, "day/night OT"):
            _cmd_upsert_week(_args(ot_rn=7), self.db_path)
        row = self._row()
        self.assertEqual(row.ot_rn, 2)
        m = compute_week_metrics(row, [], [])
        self.assertEqual(m.ot_shifts, 2)

    def test_non_ot_update_on_imported_week_still_allowed(self):
        with session_scope(self.db_path) as session:
            session.add(
                WeeklyStaffing(
                    week_start=WEEK,
                    filled_day=50,
                    filled_night=28,
                    ot_rn_day=2,
                    ot_rn=2,
                    ot_shifts=2,
                )
            )
            session.commit()
        _cmd_upsert_week(_args(filled_day=56), self.db_path)
        self.assertEqual(self._row().filled_day, 56)


if __name__ == "__main__":
    unittest.main()
