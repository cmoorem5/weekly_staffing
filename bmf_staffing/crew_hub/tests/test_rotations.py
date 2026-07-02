"""Comm rotations, work-type tagging, and drag-and-drop move/swap APIs."""

import datetime as dt
import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crew_hub.models import CommRotation, CommShiftAssignment, CommStaffMember
from crew_hub.services import apply_rotations_for_range

JULY_1 = dt.date(2026, 7, 1)
JULY_31 = dt.date(2026, 7, 31)


class RotationPatternTests(TestCase):
    def setUp(self):
        self.member = CommStaffMember.objects.create(name="Comms Test-Alpha")

    def test_cycle_pattern_four_on_four_off(self):
        rotation = CommRotation.objects.create(
            member=self.member,
            seat="D",
            pattern_type=CommRotation.PATTERN_CYCLE,
            days_on=4,
            days_off=4,
            anchor_date=JULY_1,
        )
        on_days = [d for d in range(1, 17) if rotation.works_on(dt.date(2026, 7, d))]
        self.assertEqual(on_days, [1, 2, 3, 4, 9, 10, 11, 12])

    def test_weekly_pattern(self):
        rotation = CommRotation.objects.create(
            member=self.member,
            seat="N",
            pattern_type=CommRotation.PATTERN_WEEKLY,
            weekdays="0,2",  # Mon, Wed
            anchor_date=JULY_1,
        )
        # 2026-07-06 is a Monday, 2026-07-08 a Wednesday.
        self.assertTrue(rotation.works_on(dt.date(2026, 7, 6)))
        self.assertTrue(rotation.works_on(dt.date(2026, 7, 8)))
        self.assertFalse(rotation.works_on(dt.date(2026, 7, 7)))

    def test_pattern_respects_anchor_end_and_active(self):
        rotation = CommRotation.objects.create(
            member=self.member,
            seat="D",
            pattern_type=CommRotation.PATTERN_CYCLE,
            days_on=7,
            days_off=0,
            anchor_date=dt.date(2026, 7, 10),
            end_date=dt.date(2026, 7, 20),
        )
        self.assertFalse(rotation.works_on(dt.date(2026, 7, 9)))
        self.assertTrue(rotation.works_on(dt.date(2026, 7, 10)))
        self.assertTrue(rotation.works_on(dt.date(2026, 7, 20)))
        self.assertFalse(rotation.works_on(dt.date(2026, 7, 21)))
        rotation.active = False
        self.assertFalse(rotation.works_on(dt.date(2026, 7, 15)))

    def test_apply_creates_assignments_without_overwriting(self):
        CommRotation.objects.create(
            member=self.member,
            seat="D",
            pattern_type=CommRotation.PATTERN_CYCLE,
            days_on=4,
            days_off=4,
            anchor_date=JULY_1,
        )
        # A manual entry on a rotation day must survive.
        other = CommStaffMember.objects.create(name="Comms Test-Bravo")
        CommShiftAssignment.objects.create(date=JULY_1, seat="D", member=other)

        created, skipped = apply_rotations_for_range(JULY_1, JULY_31)
        # July has 16 on-days for 4/4 anchored on the 1st; one was taken.
        self.assertEqual(created, 15)
        self.assertEqual(skipped, 1)
        self.assertEqual(
            CommShiftAssignment.objects.get(date=JULY_1, seat="D").member, other
        )
        # Re-applying is a no-op.
        created2, skipped2 = apply_rotations_for_range(JULY_1, JULY_31)
        self.assertEqual(created2, 0)
        self.assertEqual(skipped2, 16)


class CalendarApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("comm-test", password="pw")
        self.client.login(username="comm-test", password="pw")
        self.member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        self.assignment = CommShiftAssignment.objects.create(
            date=JULY_1, seat="D", member=self.member
        )

    def _post_json(self, url, body):
        return self.client.post(
            url, data=json.dumps(body), content_type="application/json"
        )

    def test_work_type_api_round_trip(self):
        url = reverse("crew_hub:api_comm_work_type", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"work_type": "overtime"})
        self.assertEqual(response.status_code, 200)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.work_type, CommShiftAssignment.WORK_OT)
        self.assertEqual(self.assignment.name_with_tag, "Comms Test-Alpha (OT)")

        response = self._post_json(url, {"work_type": "bogus"})
        self.assertEqual(response.status_code, 400)

    def test_move_to_empty_day(self):
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "moved")
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.date, dt.date(2026, 7, 5))

    def test_move_onto_occupied_seat_swaps(self):
        other_member = CommStaffMember.objects.create(name="Comms Test-Bravo")
        other = CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 5), seat="D", member=other_member
        )
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.json()["result"], "swapped")
        self.assignment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.assignment.date, dt.date(2026, 7, 5))
        self.assertEqual(other.date, JULY_1)

    def test_remove_api(self):
        url = reverse("crew_hub:api_comm_remove", kwargs={"pk": self.assignment.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CommShiftAssignment.objects.filter(pk=self.assignment.pk).exists()
        )

    def test_apis_require_login(self):
        self.client.logout()
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.status_code, 302)


class WorkTypeReportPullTests(TestCase):
    def test_report_pull_includes_work_type_tag(self):
        from crew_hub.services import get_or_create_report

        member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        CommShiftAssignment.objects.create(
            date=JULY_1,
            seat="D",
            member=member,
            work_type=CommShiftAssignment.WORK_OT,
        )
        report, _ = get_or_create_report(JULY_1)
        self.assertEqual(
            report.comm_entries.get(seat="D").name, "Comms Test-Alpha (OT)"
        )
