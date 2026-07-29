"""Comm Center day editor: load people onto a day, then seat them.

The seat is an attribute of a person's row, not the key rows are created
against, so several people can share one seat and a person can sit on a
day before anyone decides which seat they are taking.
"""

import datetime as dt

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from crew_hub.models import CommShiftAssignment, CommStaffMember
from crew_hub.services import get_or_create_report

JULY_1 = dt.date(2026, 7, 1)


class CommDayEditorTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("comm-mgr", password="pw")
        user.user_permissions.add(Permission.objects.get(codename="manage_schedules"))
        self.client.login(username="comm-mgr", password="pw")
        self.alpha = CommStaffMember.objects.create(name="Comms Test-Alpha")
        self.bravo = CommStaffMember.objects.create(name="Comms Test-Bravo")
        self.url = reverse("crew_hub:comm_day", kwargs={"date_str": "2026-07-01"})

    def _add(self, *members, name=""):
        return self.client.post(
            self.url,
            {
                "action": "add",
                "add_person": [m.pk for m in members],
                "add_name": name,
            },
        )

    def _save(self, **fields):
        return self.client.post(self.url, {"action": "save", **fields})

    def _context(self):
        return self.client.get(self.url).context

    # --- adding people ---------------------------------------------------

    def test_added_people_start_unassigned(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self.assertEqual(assignment.member, self.alpha)
        self.assertEqual(assignment.seat, "")

    def test_several_people_load_onto_a_day_in_one_go(self):
        self._add(self.alpha, self.bravo)
        self.assertEqual(CommShiftAssignment.objects.filter(date=JULY_1).count(), 2)
        context = self._context()
        self.assertEqual(context["unassigned_count"], 2)
        self.assertEqual(len(context["rows"]), 2)

    def test_adding_someone_twice_is_reported_not_duplicated(self):
        self._add(self.alpha)
        response = self.client.post(
            self.url,
            {"action": "add", "add_person": self.alpha.pk},
            follow=True,
        )
        self.assertEqual(CommShiftAssignment.objects.filter(date=JULY_1).count(), 1)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("already on this day" in m for m in messages))

    def test_typed_name_needs_no_roster_entry(self):
        self._add(name="Ride-Along Rita")
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self.assertEqual(assignment.display_name, "Ride-Along Rita")
        self.assertIsNone(assignment.member_id)

    def test_add_with_nothing_picked_reports_an_error(self):
        response = self.client.post(self.url, {"action": "add"}, follow=True)
        messages = [str(m) for m in response.context["messages"]]
        self.assertTrue(any("Pick at least one person" in m for m in messages))

    # --- seating and hours -----------------------------------------------

    def test_two_people_can_share_one_seat(self):
        """The whole point: no invented seat codes to double up a day."""
        self._add(self.alpha, self.bravo)
        rows = CommShiftAssignment.objects.filter(date=JULY_1)
        self._save(**{f"slot_{a.pk}": "D" for a in rows})
        seated = CommShiftAssignment.objects.filter(date=JULY_1, seat="D")
        self.assertEqual(seated.count(), 2)
        self.assertEqual(
            {a.member.name for a in seated},
            {"Comms Test-Alpha", "Comms Test-Bravo"},
        )

    def test_per_person_hours_override(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self._save(
            **{
                f"slot_{assignment.pk}": "D",
                f"hours_{assignment.pk}": "6",
                f"wt_{assignment.pk}": "overtime",
            }
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.hours, 6.0)
        self.assertEqual(assignment.paid_hours, 6.0)
        self.assertEqual(assignment.work_type, "overtime")

    def test_blank_hours_falls_back_to_the_seat_standard(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self._save(**{f"slot_{assignment.pk}": "D", f"hours_{assignment.pk}": ""})
        assignment.refresh_from_db()
        self.assertIsNone(assignment.hours)
        self.assertEqual(assignment.paid_hours, 12.0)

    def test_out_of_range_hours_are_ignored(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self._save(**{f"slot_{assignment.pk}": "D", f"hours_{assignment.pk}": "99"})
        assignment.refresh_from_db()
        self.assertIsNone(assignment.hours)

    def test_unknown_seat_code_falls_back_to_unassigned(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self._save(**{f"slot_{assignment.pk}": "BOGUS"})
        assignment.refresh_from_db()
        self.assertEqual(assignment.seat, "")

    def test_a_seat_can_be_handed_back(self):
        self._add(self.alpha)
        assignment = CommShiftAssignment.objects.get(date=JULY_1)
        self._save(**{f"slot_{assignment.pk}": "D"})
        self._save(**{f"slot_{assignment.pk}": ""})
        assignment.refresh_from_db()
        self.assertEqual(assignment.seat, "")
        self.assertTrue(CommShiftAssignment.objects.filter(date=JULY_1).exists())

    # --- ordering and removal --------------------------------------------

    def test_unassigned_rows_sort_first(self):
        self._add(self.alpha, self.bravo)
        alpha_row = CommShiftAssignment.objects.get(date=JULY_1, member=self.alpha)
        self._save(**{f"slot_{alpha_row.pk}": "D"})
        rows = self._context()["rows"]
        self.assertTrue(rows[0]["unassigned"])
        self.assertEqual(rows[0]["name"], "Comms Test-Bravo")

    def test_remove_drops_only_the_checked_row(self):
        self._add(self.alpha, self.bravo)
        alpha_row = CommShiftAssignment.objects.get(date=JULY_1, member=self.alpha)
        self._save(**{f"remove_{alpha_row.pk}": "on"})
        remaining = CommShiftAssignment.objects.filter(date=JULY_1)
        self.assertEqual(remaining.count(), 1)
        self.assertEqual(remaining.first().member, self.bravo)

    def test_repeat_replaces_following_days_with_a_copy(self):
        self._add(self.alpha, self.bravo)
        rows = CommShiftAssignment.objects.filter(date=JULY_1)
        # A stale row on a target day must be cleared, not merged into.
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 2), seat="N", display_name="Stale"
        )
        self._save(
            repeat_until="2026-07-03",
            **{f"slot_{a.pk}": "D" for a in rows},
        )
        for day in (JULY_1, dt.date(2026, 7, 2), dt.date(2026, 7, 3)):
            seated = CommShiftAssignment.objects.filter(date=day)
            self.assertEqual(seated.count(), 2, day)
            self.assertEqual({a.seat for a in seated}, {"D"}, day)
        self.assertFalse(
            CommShiftAssignment.objects.filter(display_name="Stale").exists()
        )

    # --- downstream -------------------------------------------------------

    def test_shared_seat_shows_both_names_on_the_daily_report(self):
        self._add(self.alpha, self.bravo)
        rows = CommShiftAssignment.objects.filter(date=JULY_1)
        self._save(**{f"slot_{a.pk}": "D" for a in rows})
        report, _ = get_or_create_report(JULY_1)
        self.assertEqual(
            report.comm_entries.get(seat="D").name,
            "Comms Test-Alpha / Comms Test-Bravo",
        )

    def test_unassigned_people_stay_off_the_daily_report(self):
        self._add(self.alpha)
        report, _ = get_or_create_report(JULY_1)
        self.assertEqual(report.comm_entries.get(seat="D").name, "")

    def test_month_view_counts_covered_seats_not_bodies(self):
        self._add(self.alpha, self.bravo)
        rows = CommShiftAssignment.objects.filter(date=JULY_1)
        self._save(**{f"slot_{a.pk}": "D" for a in rows})
        response = self.client.get(reverse("crew_hub:comm_month"), {"month": "2026-07"})
        cell = response.context["cells"][JULY_1]
        self.assertEqual(cell["filled"], 1)
        self.assertEqual(len(cell["chips"]), 2)
