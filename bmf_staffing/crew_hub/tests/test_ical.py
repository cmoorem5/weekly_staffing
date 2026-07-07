"""Personal iCal feed: token auth, event windows, reset."""

import datetime as dt

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crew_hub.ical import parse_seat_window
from crew_hub.models import (
    CalendarFeedToken,
    CommShiftAssignment,
    CommStaffMember,
    DutyAssignment,
    DutyOfficer,
)

TODAY = dt.date.today()


class SeatWindowTests(TestCase):
    def test_day_seat(self):
        start, end, next_day = parse_seat_window("0630–1830")
        self.assertEqual((start.hour, start.minute), (6, 30))
        self.assertEqual((end.hour, end.minute), (18, 30))
        self.assertFalse(next_day)

    def test_overnight_seat(self):
        start, end, next_day = parse_seat_window("1830–0630")
        self.assertTrue(next_day)

    def test_blank_is_none(self):
        self.assertIsNone(parse_seat_window(""))


class CalendarFeedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("staffer", password="pw")
        self.member = CommStaffMember.objects.create(
            name="Comms Test-Alpha", user=self.user
        )
        self.officer = DutyOfficer.objects.create(
            name="Duty Test-Alpha", role="AOC", user=self.user
        )
        self.token = CalendarFeedToken.for_user(self.user)

    def _feed(self, token=None):
        return self.client.get(
            reverse(
                "crew_hub:calendar_feed",
                kwargs={"token": token or self.token.token},
            )
        )

    def test_feed_contains_comm_and_duty_events(self):
        date = TODAY + dt.timedelta(days=3)
        CommShiftAssignment.objects.create(date=date, seat="D", member=self.member)
        DutyAssignment.objects.create(
            date=date, role="AOC", officer=self.officer, work_type="overtime"
        )
        response = self._feed()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        body = response.content.decode()
        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn("SUMMARY:Comm D", body)
        self.assertIn(f"DTSTART:{date:%Y%m%d}T063000", body)
        self.assertIn(f"DTEND:{date:%Y%m%d}T183000", body)
        self.assertIn("SUMMARY:Duty AOC (OT)", body)
        self.assertIn(f"DTSTART;VALUE=DATE:{date:%Y%m%d}", body)

    def test_overnight_seat_ends_next_day(self):
        date = TODAY + dt.timedelta(days=2)
        CommShiftAssignment.objects.create(date=date, seat="N", member=self.member)
        body = self._feed().content.decode()
        self.assertIn(f"DTSTART:{date:%Y%m%d}T183000", body)
        self.assertIn(f"DTEND:{date + dt.timedelta(days=1):%Y%m%d}T063000", body)

    def test_bad_token_404(self):
        self.assertEqual(self._feed(token="not-a-token").status_code, 404)

    def test_inactive_user_404(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self._feed().status_code, 404)

    def test_reset_rotates_token(self):
        old = self.token.token
        self.client.login(username="staffer", password="pw")
        self.client.post(reverse("crew_hub:calendar_feed_reset"))
        self.token.refresh_from_db()
        self.assertNotEqual(self.token.token, old)
        self.assertEqual(self._feed(token=old).status_code, 404)
        self.assertEqual(self._feed().status_code, 200)

    def test_my_schedule_shows_feed_url(self):
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("crew_hub:my_schedule"))
        self.assertContains(response, "feed.ics")
        self.assertContains(response, "Subscribe in your calendar")
