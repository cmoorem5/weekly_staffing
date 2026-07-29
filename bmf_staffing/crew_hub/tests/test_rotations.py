"""Comm rotations, work-type tagging, and drag-and-drop move/swap APIs."""

import datetime as dt
import json

from django.contrib.auth.models import Permission, User
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

    def test_pitman_pattern_two_two_three(self):
        # Anchored on a Sunday: 2 on, 2 off, 3 on, 2 off, 2 on, 3 off.
        anchor = dt.date(2026, 7, 19)
        rotation = CommRotation.objects.create(
            member=self.member,
            seat="D",
            pattern_type=CommRotation.PATTERN_PITMAN,
            anchor_date=anchor,
        )
        on_offsets = [
            offset
            for offset in range(28)
            if rotation.works_on(anchor + dt.timedelta(days=offset))
        ]
        self.assertEqual(on_offsets, [0, 1, 4, 5, 6, 9, 10, 14, 15, 18, 19, 20, 23, 24])
        # 7 shifts per 14-day cycle.
        self.assertEqual(len(on_offsets), 14)
        self.assertIn("2-2-3", rotation.pattern_label)

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

    def _four_on_four_off(self, member=None, seat="D"):
        return CommRotation.objects.create(
            member=member or self.member,
            seat=seat,
            pattern_type=CommRotation.PATTERN_CYCLE,
            days_on=4,
            days_off=4,
            anchor_date=JULY_1,
        )

    def test_apply_skips_days_the_person_already_works(self):
        self._four_on_four_off()
        # A manual entry for this person must survive untouched, whatever
        # seat it is in — they are already on the schedule that day.
        CommShiftAssignment.objects.create(
            date=JULY_1, seat="N", member=self.member, note="manual"
        )

        created, skipped = apply_rotations_for_range(JULY_1, JULY_31)
        # July has 16 on-days for 4/4 anchored on the 1st; one was taken.
        self.assertEqual(created, 15)
        self.assertEqual(skipped, 1)
        july_1 = CommShiftAssignment.objects.get(date=JULY_1, member=self.member)
        self.assertEqual(july_1.seat, "N")
        self.assertEqual(july_1.note, "manual")
        # Re-applying is a no-op.
        created2, skipped2 = apply_rotations_for_range(JULY_1, JULY_31)
        self.assertEqual(created2, 0)
        self.assertEqual(skipped2, 16)

    def test_apply_lets_two_people_share_one_seat(self):
        """Seats are not exclusive: neither rotation loses its days."""
        other = CommStaffMember.objects.create(name="Comms Test-Bravo")
        self._four_on_four_off()
        self._four_on_four_off(member=other)

        created, skipped = apply_rotations_for_range(JULY_1, JULY_31)
        self.assertEqual(created, 32)
        self.assertEqual(skipped, 0)
        july_1_seat_d = CommShiftAssignment.objects.filter(date=JULY_1, seat="D")
        self.assertEqual(july_1_seat_d.count(), 2)
        self.assertEqual(
            {a.member.name for a in july_1_seat_d},
            {"Comms Test-Alpha", "Comms Test-Bravo"},
        )

    def test_apply_ignores_free_text_rows_when_deduping(self):
        """A typed-in name has no roster identity, so it blocks nobody."""
        self._four_on_four_off()
        CommShiftAssignment.objects.create(date=JULY_1, seat="D", display_name="OPEN")

        created, skipped = apply_rotations_for_range(JULY_1, JULY_31)
        self.assertEqual(created, 16)
        self.assertEqual(skipped, 0)
        self.assertEqual(
            CommShiftAssignment.objects.filter(date=JULY_1, seat="D").count(), 2
        )


class RotationFormTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("rot-mgr", password="pw")
        self.user.user_permissions.add(
            Permission.objects.get(codename="manage_schedules")
        )
        self.client.login(username="rot-mgr", password="pw")
        self.member = CommStaffMember.objects.create(name="Comms Test-Alpha")

    def _add(self, follow=False, **extra):
        data = {
            "action": "add",
            "person": self.member.pk,
            "slot": "D",
            "anchor_date": "2026-07-19",
            **extra,
        }
        return self.client.post(reverse("crew_hub:comm_rotations"), data, follow=follow)

    def test_manage_page_offers_pitman_option(self):
        response = self.client.get(reverse("crew_hub:comm_rotations"))
        self.assertContains(response, 'value="pitman"')
        self.assertContains(response, "2 on / 2 off / 3 on / 2 off / 2 on / 3 off")

    def test_add_pitman_rotation(self):
        response = self._add(pattern_type="pitman")
        self.assertEqual(response.status_code, 302)
        rotation = CommRotation.objects.get(member=self.member)
        self.assertEqual(rotation.pattern_type, CommRotation.PATTERN_PITMAN)
        self.assertTrue(rotation.works_on(dt.date(2026, 7, 19)))
        self.assertFalse(rotation.works_on(dt.date(2026, 7, 21)))

    def test_unknown_pattern_type_rejected(self):
        response = self._add(pattern_type="bogus")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CommRotation.objects.exists())

    def test_overlapping_same_seat_rotation_is_not_a_conflict(self):
        """Sharing a seat is legal now, so adding one raises no warning."""
        other_member = CommStaffMember.objects.create(name="Comms Test-Bravo")
        CommRotation.objects.create(
            member=other_member,
            seat="D",
            pattern_type=CommRotation.PATTERN_PITMAN,
            anchor_date=dt.date(2026, 7, 19),
        )
        response = self._add(pattern_type="pitman", follow=True)
        messages = [str(m) for m in response.context["messages"]]
        self.assertFalse(any("overlaps" in m for m in messages))
        self.assertEqual(CommRotation.objects.filter(seat="D").count(), 2)


class SeedCommTechsTests(TestCase):
    def test_seed_command_is_idempotent(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("seed_comm_techs", stdout=StringIO())
        self.assertEqual(CommStaffMember.objects.count(), 18)
        self.assertTrue(CommStaffMember.objects.filter(name="Bilodeau, Henry").exists())
        call_command("seed_comm_techs", stdout=StringIO())
        self.assertEqual(CommStaffMember.objects.count(), 18)


class CalendarApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("comm-test", password="pw")
        self.user.user_permissions.add(
            Permission.objects.get(codename="manage_schedules")
        )
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

    def test_move_onto_occupied_seat_stacks(self):
        """Dropping onto a taken seat joins it — nobody gets displaced."""
        other_member = CommStaffMember.objects.create(name="Comms Test-Bravo")
        other = CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 5), seat="D", member=other_member
        )
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.json()["result"], "moved")
        self.assignment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.assignment.date, dt.date(2026, 7, 5))
        self.assertEqual(other.date, dt.date(2026, 7, 5))
        self.assertEqual(
            CommShiftAssignment.objects.filter(
                date=dt.date(2026, 7, 5), seat="D"
            ).count(),
            2,
        )

    def test_move_onto_a_day_the_person_already_works_is_rejected(self):
        CommShiftAssignment.objects.create(
            date=dt.date(2026, 7, 5), seat="N", member=self.member
        )
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.status_code, 409)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.date, JULY_1)

    def test_reseat_to_empty_seat(self):
        url = reverse("crew_hub:api_comm_reseat", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"slot": "N"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], "moved")
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.seat, "N")
        self.assertEqual(self.assignment.date, JULY_1)

    def test_reseat_onto_occupied_seat_shares_it(self):
        other_member = CommStaffMember.objects.create(name="Comms Test-Bravo")
        other = CommShiftAssignment.objects.create(
            date=JULY_1, seat="N", member=other_member
        )
        url = reverse("crew_hub:api_comm_reseat", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"slot": "N"})
        self.assertEqual(response.json()["result"], "moved")
        self.assignment.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.assignment.seat, "N")
        self.assertEqual(other.seat, "N")

    def test_reseat_to_unassigned(self):
        """Handing a seat back keeps the person on the day."""
        url = reverse("crew_hub:api_comm_reseat", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"slot": ""})
        self.assertEqual(response.status_code, 200)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.seat, "")
        self.assertEqual(self.assignment.date, JULY_1)

    def test_reseat_unknown_seat_rejected(self):
        url = reverse("crew_hub:api_comm_reseat", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"slot": "BOGUS"})
        self.assertEqual(response.status_code, 400)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.seat, "D")

    def test_duty_rerole_api(self):
        from crew_hub.models import DutyAssignment, DutyOfficer

        officer = DutyOfficer.objects.create(name="Duty Test-Alpha", role="AOC")
        assignment = DutyAssignment.objects.create(
            date=JULY_1, role="AOC", officer=officer
        )
        url = reverse("crew_hub:api_duty_rerole", kwargs={"pk": assignment.pk})
        response = self._post_json(url, {"slot": "MDOC"})
        self.assertEqual(response.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.role, "MDOC")

    def test_reseat_requires_manage_permission(self):
        User.objects.create_user("viewer", password="pw")
        self.client.login(username="viewer", password="pw")
        url = reverse("crew_hub:api_comm_reseat", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"seat": "N"})
        self.assertEqual(response.status_code, 403)

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

    def test_apis_require_manage_permission(self):
        User.objects.create_user("viewer", password="pw")
        self.client.login(username="viewer", password="pw")
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.status_code, 403)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.date, JULY_1)

    def test_scheduler_edit_requires_manage_permission(self):
        User.objects.create_user("viewer", password="pw")
        self.client.login(username="viewer", password="pw")
        response = self.client.post(
            reverse("crew_hub:comm_day", kwargs={"date_str": "2026-07-08"}),
            {"name_D": "Should Not-Persist"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            CommShiftAssignment.objects.filter(date=dt.date(2026, 7, 8)).exists()
        )

    def test_managers_group_grants_schedule_access(self):
        from django.contrib.auth.models import Group

        viewer = User.objects.create_user("grouped", password="pw")
        viewer.groups.add(Group.objects.get(name="Crew Hub Managers"))
        self.client.login(username="grouped", password="pw")
        url = reverse("crew_hub:api_comm_move", kwargs={"pk": self.assignment.pk})
        response = self._post_json(url, {"date": "2026-07-05"})
        self.assertEqual(response.status_code, 200)


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
