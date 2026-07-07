"""Duty officer roles: bulk add, role assignment, and role-aware pickers."""

import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crew_hub import roles
from crew_hub.models import DutyAssignment, DutyOfficer, DutyRotation
from crew_hub.views.schedulers import _parse_bulk_roster

TODAY = dt.date.today()


def make_manager(username="manager"):
    user = User.objects.create_user(username, password="pw")
    roles.set_level(user, roles.LEVEL_MANAGER)
    return user


class BulkParserTests(TestCase):
    def test_accepts_common_separators_and_aliases(self):
        people, no_role = _parse_bulk_roster(
            "Jane Smith, AOC\n"
            "John Doe — MDOC\n"
            "Alex Rivera ITC\n"
            "Sam Blood; blood\n"
            "Pat Kid - pedi\n"
            "\n"
        )
        self.assertEqual(no_role, [])
        self.assertEqual(
            people,
            [
                ("Jane Smith", "AOC"),
                ("John Doe", "MDOC"),
                ("Alex Rivera", "ITOC"),
                ("Sam Blood", "BPM"),
                ("Pat Kid", "PEDIDOC"),
            ],
        )

    def test_unknown_role_keeps_whole_line_as_name(self):
        people, no_role = _parse_bulk_roster("Bob Jones, AOX")
        self.assertEqual(people, [("Bob Jones, AOX", "")])
        self.assertEqual(no_role, ["Bob Jones, AOX"])

    def test_plain_name_has_no_role(self):
        people, no_role = _parse_bulk_roster("Solo Name")
        self.assertEqual(people, [("Solo Name", "")])
        self.assertEqual(no_role, [])


class DutyRosterRoleTests(TestCase):
    def setUp(self):
        make_manager()
        self.client.login(username="manager", password="pw")

    def test_add_with_role(self):
        self.client.post(
            reverse("crew_hub:duty_roster"),
            {"action": "add", "name": "Duty Test-Alpha", "duty_role": "AOC"},
        )
        officer = DutyOfficer.objects.get(name="Duty Test-Alpha")
        self.assertEqual(officer.role, "AOC")

    def test_bulk_add_creates_and_updates_roles(self):
        DutyOfficer.objects.create(name="Duty Test-Alpha", role="")
        self.client.post(
            reverse("crew_hub:duty_roster"),
            {
                "action": "bulk",
                "people": "Duty Test-Alpha, MDOC\nDuty Test-Bravo — AOC",
            },
        )
        self.assertEqual(DutyOfficer.objects.get(name="Duty Test-Alpha").role, "MDOC")
        self.assertEqual(DutyOfficer.objects.get(name="Duty Test-Bravo").role, "AOC")

    def test_set_role_action(self):
        officer = DutyOfficer.objects.create(name="Duty Test-Alpha")
        self.client.post(
            reverse("crew_hub:duty_roster"),
            {"action": "set_role", "pk": officer.pk, "duty_role": "ITOC"},
        )
        officer.refresh_from_db()
        self.assertEqual(officer.role, "ITOC")

    def test_non_manager_cannot_bulk_add(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.client.post(
            reverse("crew_hub:duty_roster"),
            {"action": "bulk", "people": "Duty Test-Charlie, AOC"},
        )
        self.assertFalse(DutyOfficer.objects.filter(name="Duty Test-Charlie").exists())


class DutyDayPickerTests(TestCase):
    def setUp(self):
        make_manager()
        self.client.login(username="manager", password="pw")
        self.aoc = DutyOfficer.objects.create(name="Duty Test-Aoc", role="AOC")
        self.mdoc = DutyOfficer.objects.create(name="Duty Test-Mdoc", role="MDOC")
        self.unassigned = DutyOfficer.objects.create(name="Duty Test-Open")

    def _rows(self):
        response = self.client.get(
            reverse("crew_hub:duty_day", kwargs={"date_str": TODAY.isoformat()})
        )
        return {row["role"]: row for row in response.context["rows"]}

    def test_picker_filters_by_role_and_keeps_unassigned(self):
        rows = self._rows()
        aoc_options = {o.name for o in rows["AOC"]["options"]}
        self.assertIn("Duty Test-Aoc", aoc_options)
        self.assertIn("Duty Test-Open", aoc_options)  # no role => everywhere
        self.assertNotIn("Duty Test-Mdoc", aoc_options)

    def test_current_holder_stays_after_role_change(self):
        DutyAssignment.objects.create(date=TODAY, role="AOC", officer=self.mdoc)
        rows = self._rows()
        self.assertIn(self.mdoc, rows["AOC"]["options"])


class DutyRotationRoleTests(TestCase):
    def setUp(self):
        make_manager()
        self.client.login(username="manager", password="pw")
        self.officer = DutyOfficer.objects.create(name="Duty Test-Aoc", role="AOC")

    def test_rotation_mismatch_warns_but_saves(self):
        response = self.client.post(
            reverse("crew_hub:duty_rotations"),
            {
                "action": "add",
                "person": self.officer.pk,
                "slot": "MDOC",
                "pattern_type": "cycle",
                "days_on": "4",
                "days_off": "4",
                "anchor_date": TODAY.isoformat(),
            },
            follow=True,
        )
        self.assertTrue(DutyRotation.objects.filter(officer=self.officer).exists())
        self.assertContains(response, "Heads up")
