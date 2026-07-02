"""Shift skeleton constants: counts and role-composition rules."""

from django.test import SimpleTestCase

from crew_hub import shifts


class ShiftSkeletonTests(SimpleTestCase):
    def test_expected_total_crew_rows(self):
        # BED 12 + PYM 8 + LWM 10 + MAN 6 + MHT 3
        self.assertEqual(shifts.EXPECTED_CREW_ROW_COUNT, 39)

    def test_rows_per_base(self):
        per_base = {
            base: sum(len(s.positions) for s in shifts.SHIFTS_BY_BASE[base])
            for base in shifts.BASE_ORDER
        }
        self.assertEqual(per_base, {"BED": 12, "PYM": 8, "LWM": 10, "MAN": 6, "MHT": 3})

    def test_rotor_shifts_carry_rn_emtp_pilot(self):
        for code in ("D7B", "N7B", "D7P", "N7P", "D9L", "N9L", "D11M", "D11H"):
            shift = next(s for s in shifts.CREW_SHIFTS if s.code == code)
            self.assertEqual(shift.positions, shifts.ROTOR_POSITIONS, code)

    def test_ground_critical_care_shifts_carry_rn_emtp_emt(self):
        for code in ("GR", "NG", "LG", "MG"):
            shift = next(s for s in shifts.CREW_SHIFTS if s.code == code)
            self.assertEqual(shift.positions, shifts.GROUND_CC_POSITIONS, code)

    def test_emt_only_shifts(self):
        for code in ("PG", "NP", "NL"):
            shift = next(s for s in shifts.CREW_SHIFTS if s.code == code)
            self.assertEqual(shift.positions, shifts.EMT_ONLY_POSITIONS, code)

    def test_pg_carries_exactly_one_mandatory_emt(self):
        pg = shifts.CREW_SHIFT_INDEX[("PYM", "PG")]
        self.assertEqual(pg.positions, ("EMT",))

    def test_manchester_is_day_only(self):
        self.assertEqual([s.code for s in shifts.SHIFTS_BY_BASE["MHT"]], ["D11H"])

    def test_mansfield_shifts_run_1100_to_2300(self):
        for shift in shifts.SHIFTS_BY_BASE["MAN"]:
            self.assertEqual(shift.time, "1100–2300")

    def test_combo_validation(self):
        self.assertTrue(shifts.is_valid_crew_combo("BED", "D7B", "RN"))
        self.assertTrue(shifts.is_valid_crew_combo("PYM", "PG", "EMT"))
        # Bonus RN on PG is extras-only, never a PG crew row.
        self.assertFalse(shifts.is_valid_crew_combo("PYM", "PG", "RN"))
        # Shift belongs to a different base.
        self.assertFalse(shifts.is_valid_crew_combo("MHT", "N7B", "PILOT"))
        # No EMT seat on a rotor shift.
        self.assertFalse(shifts.is_valid_crew_combo("BED", "D7B", "EMT"))
        self.assertFalse(shifts.is_valid_crew_combo("XXX", "D7B", "RN"))
