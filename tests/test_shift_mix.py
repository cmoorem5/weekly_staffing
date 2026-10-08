"""Tests for the per-person shift mix / night + weekend requirement report."""

import contextlib
import os
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

from django.test import Client
from django.urls import reverse
from staffing_tool.db import session_scope
from staffing_tool.hire_dates import (
    apply_hire_dates,
    parse_hire_date,
    parse_hire_date_csv,
)
from staffing_tool.models import StaffRosterEntry, WeeklyPersonShift, WeeklyStaffing
from staffing_tool.person_ops import PersonOpsRow
from staffing_tool.shift_mix import (
    block_windows,
    build_shift_mix,
    is_weekend_shift,
    load_shift_mix,
    required_nights,
    week_start_sunday,
    years_of_service,
)
from tests._temp_db import TempDbTestCase

from dashboard.views import helpers, shift_mix

# Sunday; the 6-week block runs 2026-01-04 .. 2026-02-14.
ANCHOR = date(2026, 1, 4)
BLOCK_END = date(2026, 2, 14)
TODAY = date(2026, 6, 1)


def _row(
    d: date,
    dn: str = "D",
    *,
    event: str = "staffed",
    svc: str = "RW",
    base: str = "Bedford",
    leave: str | None = None,
) -> PersonOpsRow:
    return PersonOpsRow(
        shift_date=d.isoformat(),
        week_start=week_start_sunday(d).isoformat(),
        role="RN",
        event_type=event,
        base_name=base,
        service_type=svc,
        day_night=dn,
        unit_code="",
        leave_type=leave,
        overtime=event == "ot",
        raw_value=leave or "",
        source_tab="",
        source_cell="",
    )


def _all_weeks(start: date, n: int) -> set[date]:
    return {start + timedelta(days=7 * i) for i in range(n)}


class RequirementRuleTests(unittest.TestCase):
    def test_night_tiers_and_boundaries(self):
        cases = {
            0: 8,
            2.99: 8,
            3: 7,
            7.99: 7,
            8: 6,
            10.99: 6,
            11: 5,
            13.99: 5,
            14: 2,
            16.99: 2,
            17: 1,
            19.99: 1,
            20: 0,
            31: 0,
        }
        for years, nights in cases.items():
            with self.subTest(years=years):
                self.assertEqual(required_nights(years), nights)

    def test_years_of_service(self):
        hire = date(2020, 3, 15)
        self.assertAlmostEqual(years_of_service(hire, date(2026, 3, 15)), 6.0, 2)
        self.assertLess(years_of_service(hire, date(2026, 3, 14)), 6.0)
        self.assertEqual(years_of_service(hire, date(2019, 1, 1)), 0.0)

    def test_leap_day_hire_date(self):
        self.assertAlmostEqual(
            years_of_service(date(2020, 2, 29), date(2026, 3, 1)), 6.0, 1
        )

    def test_weekend_is_friday_night_through_sunday_night(self):
        fri, sat, sun, mon, thu = (
            date(2026, 1, 9),
            date(2026, 1, 10),
            date(2026, 1, 11),
            date(2026, 1, 12),
            date(2026, 1, 8),
        )
        self.assertFalse(is_weekend_shift(fri, "D"))
        self.assertTrue(is_weekend_shift(fri, "N"))
        self.assertTrue(is_weekend_shift(sat, "D"))
        self.assertTrue(is_weekend_shift(sat, "N"))
        self.assertTrue(is_weekend_shift(sun, "D"))
        self.assertTrue(is_weekend_shift(sun, "N"))
        self.assertFalse(is_weekend_shift(mon, "N"))
        self.assertFalse(is_weekend_shift(thu, "N"))

    def test_block_windows_are_fixed_and_cover_the_range(self):
        windows = block_windows(ANCHOR, date(2026, 2, 1), date(2026, 3, 1))
        self.assertEqual(windows[0][1], ANCHOR)
        self.assertEqual(windows[0][2], BLOCK_END)
        self.assertEqual(windows[1][1], BLOCK_END + timedelta(days=1))
        self.assertEqual(len(windows), 2)
        # Ranges before the anchor still land on aligned blocks.
        earlier = block_windows(ANCHOR, date(2025, 12, 1), date(2025, 12, 5))
        self.assertEqual((earlier[0][1] - ANCHOR).days % 42, 0)


class BuildShiftMixTests(unittest.TestCase):
    def _build(self, rows, *, hire=date(2020, 1, 1), weeks=None, **kw):
        return build_shift_mix(
            rows,
            person="Smith, Jane",
            role="RN",
            hire_date=hire,
            range_start=kw.pop("range_start", ANCHOR),
            range_end=kw.pop("range_end", BLOCK_END),
            anchor=ANCHOR,
            data_week_starts=weeks if weeks is not None else _all_weeks(ANCHOR, 6),
            today=TODAY,
            **kw,
        )

    def test_mix_counts_exclude_ot_training_and_leave(self):
        rows = [
            _row(date(2026, 1, 5), "D", svc="RW", base="Bedford"),
            _row(date(2026, 1, 6), "N", svc="GR", base="Plymouth"),
            _row(date(2026, 1, 7), "N", svc="RW", base="Bedford"),
            _row(date(2026, 1, 8), "D", event="ot"),
            _row(date(2026, 1, 9), "D", event="training"),
            _row(date(2026, 1, 12), "D", event="leave", leave="LT-D"),
            _row(date(2026, 1, 13), "N", event="leave", leave="LT-N"),
            _row(date(2026, 1, 14), "D", event="leave", leave="SICK"),
            _row(date(2026, 1, 15), "D", event="leave", leave="WEIRD"),
        ]
        r = self._build(rows)
        self.assertEqual((r.worked.day, r.worked.night), (1, 2))
        self.assertEqual((r.worked.rw, r.worked.gr), (2, 1))
        self.assertEqual(r.day_pct, 33.3)
        self.assertEqual(r.night_pct, 66.7)
        self.assertEqual(r.rw_pct, 66.7)
        self.assertEqual(r.ot.total, 1)
        self.assertEqual(r.training, 1)
        self.assertEqual(r.leave_counts, {"LT-D": 1, "LT-N": 1, "SICK": 1, "Other": 1})
        bases = {b.base: b.total for b in r.by_base}
        self.assertEqual(bases, {"Bedford": 2, "Plymouth": 1})

    def test_weekly_target_prorates_to_weeks_with_data(self):
        r = self._build([_row(date(2026, 1, 5))])
        self.assertEqual(r.data_weeks, 6)
        self.assertEqual(r.expected_shifts, 18.0)
        # Drop two weeks of data: they are excluded, not counted as zero.
        r2 = self._build([_row(date(2026, 1, 5))], weeks=_all_weeks(ANCHOR, 4))
        self.assertEqual(r2.expected_shifts, 12.0)
        self.assertEqual(len(r2.missing_weeks), 2)

    def test_block_met_when_nights_and_weekend_requirements_reached(self):
        # Hired 2020: ~6 years at block start -> 7 nights required.
        rows = [_row(ANCHOR + timedelta(days=1 + i), "N") for i in range(4)]
        # Three weekend nights (Fri N, Sat N, Sun N) and two weekend days.
        rows += [
            _row(date(2026, 1, 9), "N"),
            _row(date(2026, 1, 10), "N"),
            _row(date(2026, 1, 11), "N"),
            _row(date(2026, 1, 17), "D"),
            _row(date(2026, 1, 18), "D"),
        ]
        r = self._build(rows)
        block = r.blocks[0]
        self.assertTrue(block.complete)
        self.assertEqual(block.required_nights, 7)
        self.assertEqual(block.nights, 7)
        self.assertEqual(block.weekend, 5)
        self.assertEqual(block.status, "Met")
        self.assertEqual(r.nights_pct, 100.0)
        self.assertEqual(r.weekend_pct, 100.0)

    def test_block_short_and_ot_never_satisfies_a_requirement(self):
        rows = [
            _row(date(2026, 1, 10), "N"),
            _row(date(2026, 1, 11), "N", event="ot"),
            _row(date(2026, 1, 17), "N", event="ot"),
        ]
        block = self._build(rows).blocks[0]
        self.assertEqual(block.nights, 1)
        self.assertEqual(block.weekend, 1)
        self.assertEqual(block.ot, 2)
        self.assertEqual(block.status, "Short")

    def test_block_with_missing_week_is_not_scored(self):
        r = self._build([_row(date(2026, 1, 10), "N")], weeks=_all_weeks(ANCHOR, 5))
        block = r.blocks[0]
        self.assertFalse(block.complete)
        self.assertEqual(block.status, "Incomplete data")
        self.assertEqual(r.scored_blocks, [])
        self.assertIsNone(r.nights_pct)

    def test_unfinished_block_is_in_progress(self):
        r = build_shift_mix(
            [],
            person="x",
            role="RN",
            hire_date=date(2020, 1, 1),
            range_start=ANCHOR,
            range_end=BLOCK_END,
            anchor=ANCHOR,
            data_week_starts=_all_weeks(ANCHOR, 6),
            today=date(2026, 1, 20),
        )
        self.assertEqual(r.blocks[0].status, "In progress")

    def test_missing_hire_date_skips_night_scoring_but_keeps_weekend(self):
        rows = [_row(date(2026, 1, 10), "D")] * 5
        r = self._build(rows, hire=None)
        block = r.blocks[0]
        self.assertIsNone(block.required_nights)
        self.assertEqual(block.weekend, 5)
        self.assertEqual(block.status, "Met")
        self.assertIsNone(r.nights_pct)

    def test_block_counts_include_rows_outside_selected_range(self):
        rows = [_row(date(2026, 1, 10), "N"), _row(date(2026, 2, 7), "N")]
        r = self._build(rows, range_start=date(2026, 2, 1), range_end=BLOCK_END)
        self.assertEqual(r.worked.total, 1)  # mix honours the chosen range
        self.assertEqual(r.blocks[0].nights, 2)  # block scoring uses whole block


class HireDateImportTests(TempDbTestCase):
    def test_parse_formats(self):
        self.assertEqual(parse_hire_date("2019-04-02"), date(2019, 4, 2))
        self.assertEqual(parse_hire_date("4/2/2019"), date(2019, 4, 2))
        self.assertEqual(parse_hire_date("04/02/19"), date(2019, 4, 2))
        self.assertIsNone(parse_hire_date("soon"))

    def test_csv_headers_and_errors(self):
        text = "Last Name,First Name,Role,Date of Hire\nSmith,Jane,RN,3/1/2015\nBad,Row,RN,nope\n"
        parsed, errors = parse_hire_date_csv(text)
        self.assertEqual(parsed, [("RN", "Smith", "Jane", date(2015, 3, 1))])
        self.assertEqual(len(errors), 1)
        parsed, _ = parse_hire_date_csv('Name,DOH\n"Jones, Bob",2020-01-02\n')
        self.assertEqual(parsed, [("", "Jones", "Bob", date(2020, 1, 2))])
        _, errors = parse_hire_date_csv("Foo,Bar\n1,2\n")
        self.assertTrue(errors)

    def test_apply_matches_roster_and_reports_misses(self):
        db = self.make_temp_db()
        with session_scope(db) as s:
            s.add_all(
                [
                    StaffRosterEntry(last_name="Smith", first_name="Jane", role="RN"),
                    StaffRosterEntry(last_name="Smith", first_name="Ann", role="MEDIC"),
                    StaffRosterEntry(last_name="Lee", first_name="", role="RN"),
                ]
            )
        entries = [
            ("RN", "Smith", "Jane", date(2015, 3, 1)),
            ("", "Smith", "", date(2010, 1, 1)),  # ambiguous: two Smiths
            ("", "Lee", "Pat", date(2012, 6, 6)),  # fills the blank first name row
            ("", "Nobody", "Here", date(2000, 1, 1)),
        ]
        with session_scope(db) as s:
            result = apply_hire_dates(s, entries)
        self.assertEqual(result.updated, 2)
        self.assertEqual(result.ambiguous, ["Smith"])
        self.assertEqual(result.unmatched, ["Nobody, Here"])
        with session_scope(db) as s:
            got = {
                (r.last_name, r.role): r.hire_date
                for r in s.query(StaffRosterEntry).all()
            }
        self.assertEqual(got[("Smith", "RN")], "2015-03-01")
        self.assertIsNone(got[("Smith", "MEDIC")])
        self.assertEqual(got[("Lee", "RN")], "2012-06-06")


class LoadShiftMixTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()
        with session_scope(self.db_path) as s:
            for i in range(6):
                s.add(
                    WeeklyStaffing(
                        week_start=(ANCHOR + timedelta(days=7 * i)).isoformat(),
                        filled_day=0,
                        filled_night=0,
                    )
                )
            s.add(
                StaffRosterEntry(
                    last_name="Smith",
                    first_name="Jane",
                    role="RN",
                    active=1,
                    hire_date="2020-01-01",
                )
            )
            s.flush()
            entry_id = s.query(StaffRosterEntry).one().id
            for d, dn, svc in [
                (date(2026, 1, 5), "D", "RW"),
                (date(2026, 1, 10), "N", "GR"),
                (date(2026, 1, 11), "N", "RW"),
            ]:
                s.add(
                    WeeklyPersonShift(
                        week_start=week_start_sunday(d).isoformat(),
                        person_display="Smith, Jane",
                        staff_member_id=entry_id,
                        shift_date=d.isoformat(),
                        role="RN",
                        event_type="staffed",
                        base_name="Bedford",
                        service_type=svc,
                        day_night=dn,
                    )
                )

    def test_load_reads_hire_date_and_rows(self):
        r = load_shift_mix(
            self.db_path,
            "Smith, Jane",
            ANCHOR,
            BLOCK_END,
            role="RN",
            anchor=ANCHOR,
            today=TODAY,
        )
        self.assertEqual(r.hire_date, date(2020, 1, 1))
        self.assertEqual((r.worked.day, r.worked.night), (1, 2))
        self.assertEqual(r.blocks[0].required_nights, 7)
        self.assertEqual(r.blocks[0].weekend, 2)
        self.assertEqual(r.data_weeks, 2)  # only weeks with person rows count
        self.assertEqual(len(r.missing_weeks), 4)

    def test_chart_labels_avoid_platform_specific_strftime(self):
        # "%-d" raises ValueError on Windows, where this app is deployed.
        r = load_shift_mix(
            self.db_path,
            "Smith, Jane",
            ANCHOR,
            BLOCK_END,
            role="RN",
            anchor=ANCHOR,
            today=TODAY,
        )
        payload = shift_mix._chart_payload(r)
        self.assertEqual(payload["blocks"][0]["label"], "Jan 4")

    def test_page_and_exports_render(self):
        with contextlib.ExitStack() as stack:
            for mod in (helpers, shift_mix):
                stack.enter_context(patch.object(mod, "DB_PATH", self.db_path))
            client = Client(HTTP_HOST="localhost")
            qs = {
                "person": "Smith, Jane",
                "date_start": ANCHOR.isoformat(),
                "date_end": BLOCK_END.isoformat(),
                "block_start": ANCHOR.isoformat(),
            }
            resp = client.get(reverse("shift_mix_report"), qs)
            self.assertEqual(resp.status_code, 200)
            body = resp.content.decode()
            self.assertIn("Smith, Jane", body)
            self.assertIn("chartBlocks", body)
            csv_resp = client.get(reverse("shift_mix_export_csv"), qs)
            self.assertEqual(csv_resp.status_code, 200)
            self.assertIn(b"Night %", csv_resp.content)
            xlsx_resp = client.get(reverse("shift_mix_export_xlsx"), qs)
            self.assertEqual(xlsx_resp.status_code, 200)
            self.assertTrue(xlsx_resp.content.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
