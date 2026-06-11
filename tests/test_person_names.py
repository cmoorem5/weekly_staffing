"""Tests for schedule grid person name parsing."""

import unittest
from pathlib import Path

from staffing_tool.person_names import (
    dedupe_person_names,
    emt_person_displays,
    is_likely_person_name,
    is_plausible_person_display,
    normalize_legacy_person_display,
    parse_name_cell,
    person_displays_for_role,
    rn_medic_person_displays,
)
from staffing_tool.schedule_import import parse_schedule_workbook, weekly_person_shift_mappings


class ParseNameCellTests(unittest.TestCase):
    def test_rn_medic_shift_letters_are_not_names(self):
        self.assertIsNone(parse_name_cell("D", is_col_a=True))
        self.assertIsNone(parse_name_cell("m", is_col_a=True))
        self.assertIsNone(parse_name_cell("PD", is_col_a=True))

    def test_emt_group_prefix_stripped(self):
        self.assertEqual(
            parse_name_cell("1-Chatigny, Aaron", is_col_a=True),
            "Chatigny, Aaron",
        )
        self.assertEqual(
            parse_name_cell("4-DiCredico, Abigail", is_col_a=True),
            "DiCredico, Abigail",
        )

    def test_first_last_without_comma(self):
        self.assertEqual(
            parse_name_cell("2-Austin Gold", is_col_a=True),
            "Gold, Austin",
        )
        self.assertEqual(
            parse_name_cell("1-Jonathan Tonelli", is_col_a=True),
            "Tonelli, Jonathan",
        )

    def test_slash_first_name(self):
        self.assertEqual(
            parse_name_cell("3-Lund, Jay/Charles", is_col_a=True),
            "Lund, Jay",
        )

    def test_skip_section_labels(self):
        self.assertIsNone(parse_name_cell("Link to OPS View", is_col_a=False))
        self.assertIsNone(parse_name_cell("OPEN", is_col_a=False))


class RnMedicPersonTests(unittest.TestCase):
    def test_shift_letter_plus_last_name(self):
        self.assertEqual(rn_medic_person_displays("D", "Cowart"), ["Cowart"])
        self.assertEqual(rn_medic_person_displays("m", "Ahlstedt"), ["Ahlstedt"])
        self.assertEqual(rn_medic_person_displays("P", "Bell"), ["Bell"])

    def test_last_name_only_in_b(self):
        self.assertEqual(rn_medic_person_displays("", "Bowman"), ["Bowman"])


class EmtPersonTests(unittest.TestCase):
    def test_paired_row_two_people(self):
        names = emt_person_displays("Deptula, Thomas", "Feddersen")
        self.assertEqual(names, ["Deptula, Thomas", "Feddersen"])

    def test_paired_row_dedupes_same_last_name(self):
        names = emt_person_displays("1-Chatigny, Aaron", "Chatigny")
        self.assertEqual(names, ["Chatigny, Aaron"])

    def test_screenshot_garbled_patterns(self):
        self.assertEqual(
            person_displays_for_role("EMT", "1-Krant, Dan", "Pimentel"),
            ("Krant, Dan", "Pimentel"),
        )
        self.assertEqual(
            person_displays_for_role("EMT", "2-Hardiman, John", "Morrissey"),
            ("Hardiman, John", "Morrissey"),
        )
        self.assertEqual(
            person_displays_for_role("EMT", "2-Austin Gold", "Laranjeira"),
            ("Gold, Austin", "Laranjeira"),
        )


class LegacyDisplayTests(unittest.TestCase):
    def test_rejects_garbled_labels(self):
        self.assertFalse(is_plausible_person_display("D, Cowart"))
        self.assertFalse(is_plausible_person_display("1-Chatigny, Aaron, Chatigny"))
        self.assertFalse(is_plausible_person_display("m, Ender"))

    def test_likely_person_name_rejects_shift_suffix_and_orientee(self):
        self.assertFalse(is_likely_person_name("Phillips K."))
        self.assertFalse(is_likely_person_name("Phillips R."))
        self.assertFalse(is_likely_person_name("K., Phillips"))
        self.assertFalse(is_likely_person_name("RAL, Orientee /"))
        self.assertTrue(is_likely_person_name("Cowart"))
        self.assertTrue(is_likely_person_name("Smith, Jane"))
        self.assertTrue(is_likely_person_name("Chatigny, Aaron"))

    def test_normalizes_legacy_shift_prefix(self):
        self.assertEqual(normalize_legacy_person_display("D, Cowart"), "Cowart")
        self.assertEqual(normalize_legacy_person_display("m, Ahlstedt"), "Ahlstedt")

    def test_normalizes_legacy_row_group(self):
        self.assertEqual(
            normalize_legacy_person_display("1-Krant, Dan, Pimentel"),
            "Krant, Dan",
        )


class DedupePersonNamesTests(unittest.TestCase):
    def test_prefers_full_name(self):
        self.assertEqual(
            dedupe_person_names(["Chatigny", "Chatigny, Aaron"]),
            ["Chatigny, Aaron"],
        )


class ScheduleImportPersonTests(unittest.TestCase):
    def test_real_upload_emt_and_rn_names(self):
        path = Path(__file__).resolve().parents[1] / (
            "uploads/schedule_upload_20260611T025001Z.xlsx"
        )
        if not path.is_file():
            self.skipTest("sample upload not present")
        week = "2026-05-31"
        records, _issues, _ops = parse_schedule_workbook(
            str(path), week_start=week
        )
        person_rows = weekly_person_shift_mappings(week, records)
        names = {r["person_display"] for r in person_rows}
        self.assertIn("Cowart", names)
        self.assertNotIn("D, Cowart", names)
        self.assertIn("Krant, Dan", names)
        self.assertNotIn("1-Krant, Dan, Pimentel", names)
        paired = [
            r
            for r in person_rows
            if r["source_cell"] == "K6" and r["role"] == "EMT"
        ]
        paired_names = {r["person_display"] for r in paired}
        self.assertEqual(paired_names, {"Deptula, Thomas", "Feddersen"})


if __name__ == "__main__":
    unittest.main()
