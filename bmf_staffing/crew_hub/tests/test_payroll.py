"""Duty rotations and the hours/payroll report + CSV exports."""

import datetime as dt

from django.contrib.auth.models import Permission, User
from django.test import TestCase
from django.urls import reverse

from crew_hub.models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    DutyRotation,
)
from crew_hub.payroll import build_hours_report
from crew_hub.services import apply_duty_rotations_for_range

JULY_1 = dt.date(2026, 7, 1)
JULY_31 = dt.date(2026, 7, 31)


class DutyRotationTests(TestCase):
    def setUp(self):
        self.officer = DutyOfficer.objects.create(name="Duty Test-Alpha")

    def test_apply_fills_role_without_overwriting(self):
        DutyRotation.objects.create(
            officer=self.officer,
            role="AOC",
            pattern_type=DutyRotation.PATTERN_CYCLE,
            days_on=7,
            days_off=7,
            anchor_date=JULY_1,
        )
        # Manual coverage on July 2 must survive.
        other = DutyOfficer.objects.create(name="Duty Test-Bravo")
        DutyAssignment.objects.create(
            date=dt.date(2026, 7, 2), role="AOC", officer=other
        )

        created, skipped = apply_duty_rotations_for_range(JULY_1, JULY_31)
        # 7/7 cycle in July: days 1-7, 15-21, 29-31 = 17 on-days, one taken.
        self.assertEqual(created, 16)
        self.assertEqual(skipped, 1)
        self.assertEqual(
            DutyAssignment.objects.get(date=dt.date(2026, 7, 2), role="AOC").officer,
            other,
        )
        created2, _ = apply_duty_rotations_for_range(JULY_1, JULY_31)
        self.assertEqual(created2, 0)

    def _login_manager(self) -> User:
        user = User.objects.create_user("duty-test", password="pw")
        user.user_permissions.add(Permission.objects.get(codename="manage_schedules"))
        self.client.login(username="duty-test", password="pw")
        return user

    def test_duty_work_type_api_and_report_tag(self):
        user = self._login_manager()
        assignment = DutyAssignment.objects.create(
            date=JULY_1, role="AOC", officer=self.officer
        )
        url = reverse("crew_hub:api_duty_work_type", kwargs={"pk": assignment.pk})
        response = self.client.post(
            url, data='{"work_type": "swap"}', content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.work_type, "swap")
        self.assertEqual(assignment.name_with_tag, "Duty Test-Alpha (Swap)")
        self.assertIsNotNone(user)

    def test_duty_move_swaps_occupied_role(self):
        a = DutyAssignment.objects.create(date=JULY_1, role="AOC", officer=self.officer)
        other = DutyOfficer.objects.create(name="Duty Test-Bravo")
        b = DutyAssignment.objects.create(
            date=dt.date(2026, 7, 5), role="AOC", officer=other
        )
        self._login_manager()
        url = reverse("crew_hub:api_duty_move", kwargs={"pk": a.pk})
        response = self.client.post(
            url, data='{"date": "2026-07-05"}', content_type="application/json"
        )
        self.assertEqual(response.json()["result"], "swapped")
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.date, dt.date(2026, 7, 5))
        self.assertEqual(b.date, JULY_1)


class HoursReportTests(TestCase):
    def setUp(self):
        self.member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        # 2 regular + 1 OT + 1 sick comm shifts (12h each), plus 1 duty day.
        CommShiftAssignment.objects.create(date=JULY_1, seat="D", member=self.member)
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 2), seat="D", member=self.member
        )
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 3),
            seat="D",
            member=self.member,
            work_type="overtime",
        )
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 4), seat="D", member=self.member, work_type="sick"
        )
        officer = DutyOfficer.objects.create(name="Comms Test-Alpha")
        DutyAssignment.objects.create(date=JULY_1, role="ITOC", officer=officer)
        # Someone else, to prove the person filter works.
        other = CommStaffMember.objects.create(name="Comms Test-Bravo")
        CommShiftAssignment.objects.create(date=JULY_1, seat="N", member=other)

    def test_totals_split_by_work_type(self):
        report = build_hours_report(JULY_1, JULY_31)
        totals = {t.person: t for t in report.totals}
        alpha = totals["Comms Test-Alpha"]
        self.assertEqual(alpha.regular, 24.0)
        self.assertEqual(alpha.overtime, 12.0)
        self.assertEqual(alpha.sick, 12.0)
        self.assertEqual(alpha.worked, 36.0)
        self.assertEqual(alpha.duty_days, 1)
        self.assertEqual(totals["Comms Test-Bravo"].regular, 12.0)

    def test_person_filter(self):
        report = build_hours_report(JULY_1, JULY_31, person_filter="alpha")
        self.assertEqual(len(report.totals), 1)
        self.assertEqual(report.totals[0].person, "Comms Test-Alpha")

    def test_open_seats_excluded(self):
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 6), seat="S3", display_name="OPEN"
        )
        report = build_hours_report(JULY_1, JULY_31)
        self.assertNotIn("OPEN", {t.person for t in report.totals})


class HoursReportViewTests(TestCase):
    def setUp(self):
        User.objects.create_user("pay-test", password="pw")
        self.client.login(username="pay-test", password="pw")
        member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        CommShiftAssignment.objects.create(date=JULY_1, seat="D", member=member)

    def test_report_page_renders(self):
        response = self.client.get(
            reverse("crew_hub:hours_report"),
            {"start": "2026-07-01", "end": "2026-07-31"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comms Test-Alpha")
        self.assertContains(response, "12.00")

    def test_summary_csv_download(self):
        response = self.client.get(
            reverse("crew_hub:hours_report_csv"),
            {"start": "2026-07-01", "end": "2026-07-31", "kind": "summary"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertIn("Person,Regular Hours,Overtime Hours", body)
        self.assertIn("Comms Test-Alpha,12.00", body)

    def test_detail_csv_download(self):
        response = self.client.get(
            reverse("crew_hub:hours_report_csv"),
            {"start": "2026-07-01", "end": "2026-07-31", "kind": "detail"},
        )
        body = response.content.decode()
        self.assertIn("Date,Person,Assignment,Work Type,Hours", body)
        self.assertIn(
            "2026-07-01,Comms Test-Alpha,Comm D (0630–1830),Regular,12.00", body
        )

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("crew_hub:hours_report"))
        self.assertEqual(response.status_code, 302)
