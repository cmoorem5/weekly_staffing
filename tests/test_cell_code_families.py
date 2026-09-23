"""Cell-code family rules: the qualifier never changes the classification.

Absence and return-to-work cells are written as a family code plus a free-text
qualifier the schedulers keep inventing (AT:FCCS, EDU:STABLE, AT/AIRWAY SIM,
RTW D7B, RAL MG). Those used to be enumerated one literal at a time, so every
new class name landed on the import review page as an unknown unit code and
needed a code change to clear. These tests pin both halves: every spelling that
was enumerated still classifies the same way, and an unseen qualifier on a
known family resolves without one.
"""

import unittest
from datetime import date

from openpyxl import Workbook
from staffing_tool.schedule_import import (
    _parse_grid,
    classify_leave_value,
    is_ignored_unit_value,
)

# Every value the enumerated AT/LT/SICK/BREV alias sets held, plus the AT-hours
# regex cases, mapped to the leave code each must still produce.
ENUMERATED_LEAVE_SPELLINGS: dict[str, str] = {
    "SM/AT": "AT",
    "AT/SIM": "AT",
    "AT:SIM": "AT",
    "AT:TDAC": "AT",
    "AT:FCCS": "AT",
    "EDU:TDAC": "AT",
    "AT/SM": "AT",
    "AT/ART": "AT",
    "AT:MICRO SIM": "AT",
    "AT:STABLE": "AT",
    "AT: SIM": "AT",
    "EDU:STABLE": "AT",
    "EDU:MICRO SIM": "AT",
    "AT/AIRWAY SIM": "AT",
    "AT/STABLE": "AT",
    "EDU:FCCS": "AT",
    "EDU:AIRWAY DECISION SIM": "AT",
    "AT8": "AT",
    "AT10": "AT",
    "AT12": "AT",
    "AT12/SHIFT": "AT",
    "LT8": "LT",
    "M-LT": "LT",
    "MIL (LT)": "LT",
    "LOA (MIL)": "LOA",
    "PER": "LT",
    "SICK SIM": "SICK",
    "SL": "SICK",
    "BRV": "BREV",
    "BERV": "BREV",
}

# Qualifiers no workbook has used yet: these must resolve on the family alone.
UNSEEN_LEAVE_SPELLINGS: dict[str, str] = {
    "AT:NRP": "AT",
    "AT/PALS": "AT",
    "AT:AIRWAY WORKSHOP": "AT",
    "EDU:NRP": "AT",
    "AT16": "AT",
    "AT6/SHIFT": "AT",
    "LT10": "LT",
    "SICK CALL": "SICK",
}

# Values the ignore list enumerated, and unseen pairings of the same prefixes.
IGNORED_VALUES: tuple[str, ...] = (
    "ULTRASOUND",
    "RAL D7B",
    "RTW ADMIN",
    "RTW D7B",
    "RTW D7P",
    "GR-RAL",
    "HOL",
    "PR",
    "RTW GR",
    "RTW D11B",
    "RAL MG",
    "RAL D7P",
    "ADMIN",
    "RAL D9L",
    "RAL FW",
    "SM/RAL LG",
    "CLINICAL/RAL FW",
    "EMT/FW",
    "ZZ",
    "RTW CLINICAL/ADMIN",
    "RTW D9L",
    "RTW PG",
    "LTM/AOC",
    # Not previously enumerated — the prefix rule has to carry these.
    "RTW N7B",
    "RAL PG",
    "RTW D11H",
)


def _parse_cell(cell_value: str, *, role: str = "MEDIC", manager: bool = False):
    # "Holst" is a real name in the built-in manager roster, so every caller
    # here must pass an explicit roster rather than the (Holst-inclusive)
    # default, or a "non-manager" cell silently parses as a manager row.
    wb = Workbook()
    ws = wb.active
    ws.title = "RN & Medic"
    ws["C1"] = date(2024, 1, 7)
    ws["A4"] = "MGRLAST" if manager else "Holst"
    ws["C4"] = cell_value
    mgr_upper = frozenset({"MGRLAST"}) if manager else frozenset()
    return _parse_grid(
        ws=ws,
        header_row_idx=1,
        first_row_idx=4,
        last_row_idx=4,
        role=role,
        sheet_label=f"RN & Medic ({role.title()})",
        week_start_date=date(2024, 1, 7),
        week_end_date=date(2024, 1, 13),
        manager_last_names_upper=mgr_upper,
    )


class LeaveFamilyTests(unittest.TestCase):
    def test_every_enumerated_spelling_keeps_its_leave_code(self):
        for value, expected in ENUMERATED_LEAVE_SPELLINGS.items():
            with self.subTest(value=value):
                self.assertEqual(classify_leave_value(value)[0], expected)

    def test_unseen_qualifier_resolves_on_the_family(self):
        for value, expected in UNSEEN_LEAVE_SPELLINGS.items():
            with self.subTest(value=value):
                self.assertEqual(classify_leave_value(value)[0], expected)

    def test_lt_direction_survives_in_the_display_value(self):
        # LT-D / LT-N count as LT but keep their direction on the exception grid.
        self.assertEqual(classify_leave_value("LT-D"), ("LT", "LT-D"))
        self.assertEqual(classify_leave_value("LT-N"), ("LT", "LT-N"))

    def test_bare_edu_is_left_for_the_training_classifier(self):
        # EDU alone is a training marker (SKIP_TRAINING_VALUES), not leave —
        # only a qualified EDU:<class> counts as AT.
        self.assertEqual(classify_leave_value("EDU"), ("EDU", "EDU"))

    def test_unit_codes_and_unknowns_pass_through_unchanged(self):
        for value in ("D7B", "N9L", "LTM", "ZZZ"):
            with self.subTest(value=value):
                self.assertEqual(classify_leave_value(value), (value, value))

    def test_unseen_at_qualifier_parses_as_leave_not_unknown_unit(self):
        records, issues = _parse_cell("AT:NRP")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].leave_type, "AT")
        self.assertFalse(records[0].filled)


class IgnoredValueTests(unittest.TestCase):
    def test_all_ignored_values_are_recognized(self):
        for value in IGNORED_VALUES:
            with self.subTest(value=value):
                self.assertTrue(is_ignored_unit_value(value))

    def test_real_units_are_not_ignored(self):
        for value in ("D7B", "N7P", "GR", "MG", "D11H"):
            with self.subTest(value=value):
                self.assertFalse(is_ignored_unit_value(value))

    def test_unseen_rtw_pairing_skips_instead_of_flagging_unknown(self):
        records, issues = _parse_cell("RTW N7B")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].skip_reason, "admin")
        self.assertFalse(records[0].included_in_aggregates)


class ClinicalPrefixAliasTests(unittest.TestCase):
    """ "CLINICAL/<code>" is schedulers prefixing a real code out of habit --
    unlike the leave-family qualifiers above, the qualifier IS the code
    (SIM = training, AOC = admin, ADMIN = AT leave), so each one substitutes
    to a specific target rather than resolving on the family alone."""

    def test_clinical_sim_is_training(self):
        records, issues = _parse_cell("CLINICAL/SIM")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].skip_reason, "training")
        self.assertFalse(records[0].filled)

    def test_clinical_admin_is_at_leave(self):
        records, issues = _parse_cell("CLINICAL/ADMIN")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].leave_type, "AT")
        self.assertFalse(records[0].filled)

    def test_clinical_aoc_is_admin_skip(self):
        records, issues = _parse_cell("CLINICAL/AOC")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].skip_reason, "admin")
        self.assertFalse(records[0].filled)

    def test_clinical_aoc_credits_manager_aoc_same_as_bare_aoc(self):
        records, issues = _parse_cell("CLINICAL/AOC", manager=True)
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].manager_event_type, "aoc")
        self.assertFalse(records[0].included_in_aggregates)

    def test_cinical_typo_matches_clinical(self):
        records, issues = _parse_cell("CINICAL")
        self.assertEqual(issues, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].skip_reason, "admin")


if __name__ == "__main__":
    unittest.main()
