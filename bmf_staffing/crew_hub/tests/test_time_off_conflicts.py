"""Time-off conflict lookup: correctness and query count.

The review queue lists every pending request at once, so conflicts are
resolved for all of them in a fixed number of queries rather than one
pair of queries per request.
"""

import datetime as dt

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from crew_hub import roles
from crew_hub.models import (
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
    TimeOffRequest,
)
from crew_hub.views.me import _conflicts_for

TODAY = dt.date(2026, 7, 1)


def _requester(username: str, *, comm=False, duty=False) -> User:
    user = User.objects.create_user(username, password="pw")
    if comm:
        CommStaffMember.objects.create(name=f"{username} comm", user=user)
    if duty:
        DutyOfficer.objects.create(name=f"{username} duty", user=user, role="AOC")
    return user


def _request_for(user: User, start=TODAY, days=2) -> TimeOffRequest:
    return TimeOffRequest.objects.create(
        user=user, start_date=start, end_date=start + dt.timedelta(days=days)
    )


class ConflictDetectionTests(TestCase):
    def test_comm_and_duty_days_inside_the_window_are_reported(self):
        user = _requester("alpha", comm=True, duty=True)
        CommShiftAssignment.objects.create(
            date=TODAY, seat="D", member=user.comm_profile
        )
        DutyAssignment.objects.create(
            date=TODAY + dt.timedelta(days=1), role="AOC", officer=user.duty_profile
        )
        conflicts = _conflicts_for(_request_for(user))
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(any("Comm D" in c for c in conflicts))
        self.assertTrue(any("Duty AOC" in c for c in conflicts))

    def test_days_outside_the_window_are_ignored(self):
        user = _requester("bravo", comm=True)
        CommShiftAssignment.objects.create(
            date=TODAY - dt.timedelta(days=1), seat="D", member=user.comm_profile
        )
        CommShiftAssignment.objects.create(
            date=TODAY + dt.timedelta(days=9), seat="D", member=user.comm_profile
        )
        self.assertEqual(_conflicts_for(_request_for(user)), [])

    def test_another_persons_days_are_not_attributed(self):
        mine = _requester("charlie", comm=True)
        theirs = _requester("delta", comm=True)
        CommShiftAssignment.objects.create(
            date=TODAY, seat="D", member=theirs.comm_profile
        )
        self.assertEqual(_conflicts_for(_request_for(mine)), [])

    def test_unassigned_seat_reports_as_unassigned(self):
        user = _requester("echo", comm=True)
        CommShiftAssignment.objects.create(
            date=TODAY, seat="", member=user.comm_profile
        )
        self.assertEqual(
            _conflicts_for(_request_for(user)), ["Wed Jul 1: Comm Unassigned"]
        )

    def test_user_with_no_linked_profile_has_no_conflicts(self):
        self.assertEqual(_conflicts_for(_request_for(_requester("foxtrot"))), [])


class ConflictQueryCountTests(TestCase):
    """The queue must not issue queries proportional to the request count."""

    def setUp(self):
        user = User.objects.create_user("reviewer", password="pw")
        roles.set_level(user, roles.LEVEL_REVIEWER)
        self.client.login(username="reviewer", password="pw")

    def _seed(self, count: int, tag: str) -> None:
        for i in range(count):
            user = _requester(f"{tag}{i}", comm=True, duty=True)
            CommShiftAssignment.objects.create(
                date=TODAY, seat="D", member=user.comm_profile
            )
            DutyAssignment.objects.create(
                date=TODAY, role="AOC", officer=user.duty_profile
            )
            _request_for(user)

    def _queries_for(self, count: int, tag: str) -> int:
        self._seed(count, tag)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("crew_hub:time_off_manage"))
        self.assertEqual(response.status_code, 200)
        return len(ctx)

    def test_query_count_does_not_grow_with_pending_requests(self):
        few = self._queries_for(2, "few")
        TimeOffRequest.objects.all().delete()
        CommShiftAssignment.objects.all().delete()
        DutyAssignment.objects.all().delete()
        many = self._queries_for(12, "many")
        self.assertEqual(
            few,
            many,
            f"query count grew from {few} (2 requests) to {many} (12 requests) — "
            "the conflict lookup is querying per request again",
        )
