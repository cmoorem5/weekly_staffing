"""Duty officer roles: bulk add, role assignment, and the day editor."""

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


class DutyDayEditorTests(TestCase):
    """The day editor is person-first: add people, then set their role."""

    def setUp(self):
        make_manager()
        self.client.login(username="manager", password="pw")
        self.aoc = DutyOfficer.objects.create(name="Duty Test-Aoc", role="AOC")
        self.mdoc = DutyOfficer.objects.create(name="Duty Test-Mdoc", role="MDOC")
        self.unassigned = DutyOfficer.objects.create(name="Duty Test-Open")
        self.url = reverse("crew_hub:duty_day", kwargs={"date_str": TODAY.isoformat()})

    def _context(self):
        return self.client.get(self.url).context

    def test_everyone_active_can_be_added(self):
        available = {p.name for p in self._context()["available"]}
        self.assertEqual(
            available, {"Duty Test-Aoc", "Duty Test-Mdoc", "Duty Test-Open"}
        )

    def test_adding_an_officer_defaults_to_their_rostered_role(self):
        self.client.post(self.url, {"action": "add", "add_person": self.mdoc.pk})
        assignment = DutyAssignment.objects.get(date=TODAY, officer=self.mdoc)
        self.assertEqual(assignment.role, "MDOC")

    def test_officer_without_a_role_lands_unassigned(self):
        self.client.post(self.url, {"action": "add", "add_person": self.unassigned.pk})
        assignment = DutyAssignment.objects.get(date=TODAY, officer=self.unassigned)
        self.assertEqual(assignment.role, "")
        rows = {row["name"]: row for row in self._context()["rows"]}
        self.assertTrue(rows["Duty Test-Open"]["unassigned"])
        self.assertEqual(self._context()["unassigned_count"], 1)

    def test_several_officers_can_share_one_role(self):
        self.client.post(
            self.url,
            {"action": "add", "add_person": [self.aoc.pk, self.mdoc.pk]},
        )
        rows = DutyAssignment.objects.filter(date=TODAY)
        self.client.post(
            self.url,
            {
                "action": "save",
                **{f"slot_{a.pk}": "MDOC" for a in rows},
                **{f"wt_{a.pk}": "regular" for a in rows},
            },
        )
        self.assertEqual(
            DutyAssignment.objects.filter(date=TODAY, role="MDOC").count(), 2
        )

    def test_people_already_on_the_day_are_not_offered_again(self):
        self.client.post(self.url, {"action": "add", "add_person": self.aoc.pk})
        available = {p.name for p in self._context()["available"]}
        self.assertNotIn("Duty Test-Aoc", available)

    def test_typed_name_is_added_without_a_roster_link(self):
        self.client.post(self.url, {"action": "add", "add_name": "Visiting Fellow"})
        assignment = DutyAssignment.objects.get(date=TODAY)
        self.assertEqual(assignment.display_name, "Visiting Fellow")
        self.assertIsNone(assignment.officer_id)
        self.assertEqual(assignment.role, "")

    def test_save_sets_role_work_type_hours_and_note(self):
        self.client.post(self.url, {"action": "add", "add_person": self.unassigned.pk})
        assignment = DutyAssignment.objects.get(date=TODAY)
        self.client.post(
            self.url,
            {
                "action": "save",
                f"slot_{assignment.pk}": "ITOC",
                f"wt_{assignment.pk}": "overtime",
                f"hours_{assignment.pk}": "7.5",
                f"note_{assignment.pk}": "covering the back half",
            },
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.role, "ITOC")
        self.assertEqual(assignment.work_type, "overtime")
        self.assertEqual(assignment.hours, 7.5)
        self.assertEqual(assignment.note, "covering the back half")

    def test_remove_checkbox_drops_the_row(self):
        self.client.post(self.url, {"action": "add", "add_person": self.aoc.pk})
        assignment = DutyAssignment.objects.get(date=TODAY)
        self.client.post(self.url, {"action": "save", f"remove_{assignment.pk}": "on"})
        self.assertFalse(DutyAssignment.objects.filter(date=TODAY).exists())

    def test_repeat_copies_the_whole_day_forward(self):
        self.client.post(
            self.url,
            {"action": "add", "add_person": [self.aoc.pk, self.mdoc.pk]},
        )
        self.client.post(
            self.url,
            {
                "action": "save",
                "repeat_until": (TODAY + dt.timedelta(days=2)).isoformat(),
            },
        )
        for offset in (0, 1, 2):
            day = TODAY + dt.timedelta(days=offset)
            self.assertEqual(
                DutyAssignment.objects.filter(date=day).count(), 2, f"day +{offset}"
            )

    def test_non_manager_cannot_add_people(self):
        User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")
        self.client.post(self.url, {"action": "add", "add_person": self.aoc.pk})
        self.assertFalse(DutyAssignment.objects.filter(date=TODAY).exists())


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
