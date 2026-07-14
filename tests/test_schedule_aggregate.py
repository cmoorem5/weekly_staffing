"""Tests for schedule import aggregation (OT and leave counts)."""

import unittest
from datetime import date

from staffing_tool.schedule_import import ShiftRecord, aggregate_week_from_records


def _filled_shift(**kwargs):
    defaults = {
        "date": date(2026, 1, 4),
        "base": "Bedford",
        "service_type": "RW",
        "day_night": "D",
        "role": "RN",
        "filled": True,
        "overtime": False,
        "leave_type": None,
        "source_tab": "RN & Medic",
        "source_cell": "C5",
        "raw_value": "D7B",
        "unit_code": "D7B",
        "person_display": "Smith",
        "is_manager_row": False,
    }
    defaults.update(kwargs)
    return ShiftRecord(**defaults)


class AggregateWeekFromRecordsTests(unittest.TestCase):
    def test_ot_only_on_filled_rows(self):
        records = [
            _filled_shift(role="RN", overtime=True, unit_code="D7B", source_cell="C5"),
            _filled_shift(role="MEDIC", unit_code="D7B", source_cell="C5"),
            ShiftRecord(
                date=date(2026, 1, 4),
                base="Bedford",
                service_type="RW",
                day_night="D",
                role="RN",
                filled=False,
                overtime=True,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C6",
                raw_value="D7BC",
                unit_code="D7BC",
                person_display="",
                is_manager_row=False,
            ),
        ]
        agg = aggregate_week_from_records(
            "2026-01-04",
            records,
            ({}, {}, {}, {}),
        )
        self.assertEqual(agg.filled_day, 1)
        self.assertEqual(agg.ot_rn_day, 1)
        self.assertEqual(
            agg.ot_rn_day
            + agg.ot_medic_day
            + agg.ot_emt_day
            + agg.ot_rn_night
            + agg.ot_medic_night
            + agg.ot_emt_night,
            1,
        )

    def test_leave_types_roll_up(self):
        records = [
            ShiftRecord(
                date=date(2026, 1, 4),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type="SICK",
                source_tab="RN & Medic",
                source_cell="C7",
                raw_value="SICK",
                unit_code="",
                person_display="",
                is_manager_row=False,
            ),
            ShiftRecord(
                date=date(2026, 1, 4),
                base="",
                service_type="",
                day_night="D",
                role="MEDIC",
                filled=False,
                overtime=False,
                leave_type="PFML",
                source_tab="RN & Medic",
                source_cell="C8",
                raw_value="PFML",
                unit_code="",
                person_display="",
                is_manager_row=False,
            ),
        ]
        agg = aggregate_week_from_records(
            "2026-01-04",
            records,
            ({}, {}, {}, {}),
        )
        self.assertEqual(agg.leave_sick, 1)
        self.assertEqual(agg.leave_loa, 1)

    def test_ops_view_coverage_capped_at_base_weekly_max(self):
        """OPS View counts above a base's weekly plan (e.g. the extra Bedford
        GR2/NG2 ambulances) must not report >100% base coverage or inflate
        the fixed-denominator system GR %."""
        agg = aggregate_week_from_records(
            "2026-01-04",
            [],
            (
                {"Bedford": 7, "Plymouth": 7},  # rw day
                {"Bedford": 7, "Plymouth": 9},  # rw night: Plymouth 16 > 14 cap
                {"Bedford": 9},  # gr day
                {"Bedford": 7},  # gr night: Bedford 16 > 14 cap
            ),
        )
        self.assertEqual(agg.base_gr_staffed["Bedford"], 14)
        # Night is reduced first, then day.
        self.assertEqual(agg.base_gr_staffed_day["Bedford"], 9)
        self.assertEqual(agg.base_gr_staffed_night["Bedford"], 5)
        self.assertEqual(agg.base_rw_staffed["Plymouth"], 14)
        # Within-cap counts pass through untouched.
        self.assertEqual(agg.base_rw_staffed["Bedford"], 14)
        # Filled-from-OPS fallback uses the capped counts.
        self.assertEqual(agg.filled_day + agg.filled_night, 14 + 14 + 14)

    def test_bare_rn_emt_are_not_leave_codes(self):
        """Role abbreviations in a cell are not valid absence types."""
        records = [
            ShiftRecord(
                date=date(2026, 1, 4),
                base="",
                service_type="",
                day_night="D",
                role="RN",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="RN & Medic",
                source_cell="C9",
                raw_value="RN",
                unit_code="",
                person_display="",
                is_manager_row=False,
            ),
            ShiftRecord(
                date=date(2026, 1, 4),
                base="",
                service_type="",
                day_night="D",
                role="EMT",
                filled=False,
                overtime=False,
                leave_type=None,
                source_tab="EMT",
                source_cell="C9",
                raw_value="EMT",
                unit_code="",
                person_display="",
                is_manager_row=False,
            ),
        ]
        # Parser would not emit leave_type for RN/EMT once removed from LEAVE_CODES;
        # aggregation ignores rows without leave_type.
        agg = aggregate_week_from_records(
            "2026-01-04",
            records,
            ({}, {}, {}, {}),
        )
        self.assertEqual(agg.leave_at, 0)
        self.assertEqual(agg.leave_sick, 0)
        self.assertEqual(len(agg.leave_breakdown), 0)


if __name__ == "__main__":
    unittest.main()
