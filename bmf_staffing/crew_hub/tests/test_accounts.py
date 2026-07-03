"""User-linked profiles, my-schedule, time-off workflow, notifications."""

import datetime as dt

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from crew_hub.models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    Notification,
    TimeOffRequest,
)

TODAY = dt.date.today()


def make_manager(username="manager"):
    user = User.objects.create_user(username, password="pw")
    user.groups.add(Group.objects.get(name="Crew Hub Managers"))
    return user


class MyScheduleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staffer", password="pw")
        self.member = CommStaffMember.objects.create(
            name="Comms Test-Alpha", user=self.user
        )
        self.officer = DutyOfficer.objects.create(
            name="Duty Test-Alpha", user=self.user
        )
        self.client.login(username="staffer", password="pw")

    def test_shows_linked_comm_and_duty_assignments(self):
        CommShiftAssignment.objects.create(
            date=TODAY + dt.timedelta(days=2), seat="D", member=self.member
        )
        DutyAssignment.objects.create(
            date=TODAY + dt.timedelta(days=3), role="AOC", officer=self.officer
        )
        response = self.client.get(reverse("crew_hub:my_schedule"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Comm D")
        self.assertContains(response, "Duty AOC")
        self.assertContains(response, "Comms Test-Alpha")

    def test_unlinked_user_sees_hint(self):
        User.objects.create_user("lonely", password="pw")
        self.client.login(username="lonely", password="pw")
        response = self.client.get(reverse("crew_hub:my_schedule"))
        self.assertContains(response, "isn't linked")


class RosterLinkTests(TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.client.login(username="manager", password="pw")
        self.member = CommStaffMember.objects.create(name="Comms Test-Alpha")
        self.login_user = User.objects.create_user("staffer", password="pw")

    def test_manager_links_login_to_member(self):
        self.client.post(
            reverse("crew_hub:comm_staff"),
            {"action": "link", "pk": self.member.pk, "user": self.login_user.pk},
        )
        self.member.refresh_from_db()
        self.assertEqual(self.member.user, self.login_user)

    def test_link_rejects_double_assignment(self):
        CommStaffMember.objects.create(name="Comms Test-Bravo", user=self.login_user)
        self.client.post(
            reverse("crew_hub:comm_staff"),
            {"action": "link", "pk": self.member.pk, "user": self.login_user.pk},
        )
        self.member.refresh_from_db()
        self.assertIsNone(self.member.user)

    def test_non_manager_cannot_link(self):
        self.client.login(username="staffer", password="pw")
        self.client.post(
            reverse("crew_hub:comm_staff"),
            {"action": "link", "pk": self.member.pk, "user": self.login_user.pk},
        )
        self.member.refresh_from_db()
        self.assertIsNone(self.member.user)


class TimeOffTests(TestCase):
    def setUp(self):
        self.manager = make_manager()
        self.staffer = User.objects.create_user("staffer", password="pw")
        self.member = CommStaffMember.objects.create(
            name="Comms Test-Alpha", user=self.staffer
        )

    def _submit_request(self):
        self.client.login(username="staffer", password="pw")
        start = TODAY + dt.timedelta(days=7)
        end = TODAY + dt.timedelta(days=9)
        self.client.post(
            reverse("crew_hub:time_off_submit"),
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "reason": "vacation",
            },
        )
        return TimeOffRequest.objects.get(user=self.staffer), start, end

    def test_submit_notifies_managers(self):
        request, *_ = self._submit_request()
        self.assertTrue(request.is_pending)
        self.assertTrue(
            Notification.objects.filter(
                user=self.manager, message__icontains="Time-off request"
            ).exists()
        )

    def test_approve_notifies_requester_and_lists_conflicts(self):
        request, start, _ = self._submit_request()
        CommShiftAssignment.objects.create(date=start, seat="D", member=self.member)
        self.client.login(username="manager", password="pw")
        page = self.client.get(reverse("crew_hub:time_off_manage"))
        self.assertContains(page, "scheduled day(s) in this window")

        self.client.post(
            reverse("crew_hub:time_off_decide", kwargs={"pk": request.pk}),
            {"decision": "approved", "manager_note": "Enjoy"},
        )
        request.refresh_from_db()
        self.assertEqual(request.status, TimeOffRequest.STATUS_APPROVED)
        self.assertEqual(request.decided_by, self.manager)
        note = Notification.objects.get(user=self.staffer)
        self.assertIn("approved", note.message)
        self.assertIn("Enjoy", note.message)

    def test_deny_path(self):
        request, *_ = self._submit_request()
        self.client.login(username="manager", password="pw")
        self.client.post(
            reverse("crew_hub:time_off_decide", kwargs={"pk": request.pk}),
            {"decision": "denied"},
        )
        request.refresh_from_db()
        self.assertEqual(request.status, TimeOffRequest.STATUS_DENIED)
        self.assertIn("denied", Notification.objects.get(user=self.staffer).message)

    def test_decide_requires_manager(self):
        request, *_ = self._submit_request()
        self.client.login(username="staffer", password="pw")
        self.client.post(
            reverse("crew_hub:time_off_decide", kwargs={"pk": request.pk}),
            {"decision": "approved"},
        )
        request.refresh_from_db()
        self.assertTrue(request.is_pending)


class NotificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staffer", password="pw")
        self.client.login(username="staffer", password="pw")

    def test_unread_badge_and_mark_read(self):
        Notification.objects.create(user=self.user, message="Shift moved.")
        response = self.client.get(reverse("crew_hub:notifications"))
        self.assertContains(response, "Shift moved.")
        self.assertContains(response, "1 unread")

        self.client.post(reverse("crew_hub:notifications_read"))
        self.assertFalse(
            Notification.objects.filter(user=self.user, read=False).exists()
        )

    def test_calendar_change_notifies_linked_user(self):
        member = CommStaffMember.objects.create(name="Comms Test-Alpha", user=self.user)
        assignment = CommShiftAssignment.objects.create(
            date=TODAY, seat="D", member=member
        )
        manager = make_manager()
        self.client.login(username="manager", password="pw")
        self.client.post(
            reverse("crew_hub:api_comm_work_type", kwargs={"pk": assignment.pk}),
            data='{"work_type": "overtime"}',
            content_type="application/json",
        )
        note = Notification.objects.get(user=self.user)
        self.assertIn("Overtime", note.message)
        self.assertIn(manager.get_username(), note.message)

    def test_self_change_does_not_notify_self(self):
        manager = make_manager("self-managing")
        member = CommStaffMember.objects.create(name="Comms Test-Bravo", user=manager)
        assignment = CommShiftAssignment.objects.create(
            date=TODAY, seat="N", member=member
        )
        self.client.login(username="self-managing", password="pw")
        self.client.post(
            reverse("crew_hub:api_comm_work_type", kwargs={"pk": assignment.pk}),
            data='{"work_type": "swap"}',
            content_type="application/json",
        )
        self.assertFalse(Notification.objects.filter(user=manager).exists())
