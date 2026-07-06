"""Permission levels, the Users & permissions page, and roster login creation."""

import datetime as dt

from django.contrib.auth.models import Group, User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from crew_hub import roles
from crew_hub.models import CommStaffMember, Notification, TimeOffRequest

TODAY = dt.date.today()


def make_user(username, level=roles.LEVEL_MEMBER):
    user = User.objects.create_user(username, password="pw")
    roles.set_level(user, level)
    return user


class LevelTests(TestCase):
    def test_set_and_get_level_roundtrip(self):
        user = make_user("person")
        for level in (
            roles.LEVEL_ADMIN,
            roles.LEVEL_MANAGER,
            roles.LEVEL_REVIEWER,
            roles.LEVEL_MEMBER,
        ):
            roles.set_level(user, level)
            self.assertEqual(roles.get_level(user), level)
            # Exactly one level group at a time (none for Member).
            level_groups = user.groups.filter(
                name__in=roles.LEVEL_GROUPS.values()
            ).count()
            self.assertEqual(level_groups, 0 if level == roles.LEVEL_MEMBER else 1)

    def test_superuser_is_admin(self):
        boss = User.objects.create_superuser("boss", password="pw")
        self.assertEqual(roles.get_level(boss), roles.LEVEL_ADMIN)

    def test_level_permissions(self):
        cases = {
            roles.LEVEL_ADMIN: (True, True, True),
            roles.LEVEL_MANAGER: (False, True, True),
            roles.LEVEL_REVIEWER: (False, False, True),
            roles.LEVEL_MEMBER: (False, False, False),
        }
        for level, (users, schedules, review) in cases.items():
            user = make_user(f"user-{level}", level)
            self.assertEqual(user.has_perm("crew_hub.manage_users"), users, level)
            self.assertEqual(
                user.has_perm("crew_hub.manage_schedules"), schedules, level
            )
            self.assertEqual(user.has_perm("crew_hub.review_time_off"), review, level)

    def test_create_login_generates_temp_password(self):
        user, temp_password, error = roles.create_login(
            "newbie", email="n@example.org", level=roles.LEVEL_REVIEWER
        )
        self.assertEqual(error, "")
        self.assertTrue(temp_password)
        self.assertTrue(user.check_password(temp_password))
        self.assertEqual(roles.get_level(user), roles.LEVEL_REVIEWER)

    def test_create_login_rejects_duplicate_username(self):
        make_user("taken")
        user, _, error = roles.create_login("Taken")
        self.assertIsNone(user)
        self.assertIn("already exists", error)


class ReviewerTimeOffTests(TestCase):
    def setUp(self):
        self.reviewer = make_user("reviewer", roles.LEVEL_REVIEWER)
        self.member = make_user("staffer")
        self.request = TimeOffRequest.objects.create(
            user=self.member,
            start_date=TODAY + dt.timedelta(days=7),
            end_date=TODAY + dt.timedelta(days=8),
        )

    def test_reviewer_can_open_manage_page_and_decide(self):
        self.client.login(username="reviewer", password="pw")
        response = self.client.get(reverse("crew_hub:time_off_manage"))
        self.assertEqual(response.status_code, 200)
        self.client.post(
            reverse("crew_hub:time_off_decide", kwargs={"pk": self.request.pk}),
            {"decision": "approved"},
        )
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, TimeOffRequest.STATUS_APPROVED)

    def test_member_cannot_decide(self):
        self.client.login(username="staffer", password="pw")
        self.client.post(
            reverse("crew_hub:time_off_decide", kwargs={"pk": self.request.pk}),
            {"decision": "approved"},
        )
        self.request.refresh_from_db()
        self.assertTrue(self.request.is_pending)

    def test_submission_notifies_reviewers(self):
        self.client.login(username="staffer", password="pw")
        self.client.post(
            reverse("crew_hub:time_off_submit"),
            {
                "start_date": (TODAY + dt.timedelta(days=10)).isoformat(),
                "end_date": (TODAY + dt.timedelta(days=11)).isoformat(),
            },
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.reviewer, message__icontains="Time-off request"
            ).exists()
        )


class UserAdminPageTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", roles.LEVEL_ADMIN)
        self.member = make_user("staffer")

    def test_requires_manage_users(self):
        self.client.login(username="staffer", password="pw")
        response = self.client.get(reverse("crew_hub:user_admin"))
        self.assertRedirects(response, reverse("crew_hub:hub_home"))

    def test_lists_users_with_levels(self):
        self.client.login(username="admin", password="pw")
        response = self.client.get(reverse("crew_hub:user_admin"))
        self.assertContains(response, "staffer")
        self.assertContains(response, "Add a login")

    def test_create_login_with_level(self):
        self.client.login(username="admin", password="pw")
        self.client.post(
            reverse("crew_hub:user_admin"),
            {
                "action": "create",
                "username": "newmanager",
                "email": "nm@example.org",
                "level": roles.LEVEL_MANAGER,
                "password": "",
            },
        )
        created = User.objects.get(username="newmanager")
        self.assertEqual(roles.get_level(created), roles.LEVEL_MANAGER)

    def test_change_level_and_toggle_active(self):
        self.client.login(username="admin", password="pw")
        self.client.post(
            reverse("crew_hub:user_admin"),
            {"action": "level", "pk": self.member.pk, "level": roles.LEVEL_REVIEWER},
        )
        self.member.refresh_from_db()
        self.assertEqual(roles.get_level(self.member), roles.LEVEL_REVIEWER)

        self.client.post(
            reverse("crew_hub:user_admin"),
            {"action": "toggle", "pk": self.member.pk},
        )
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)

    def test_cannot_change_own_account(self):
        self.client.login(username="admin", password="pw")
        self.client.post(
            reverse("crew_hub:user_admin"),
            {"action": "toggle", "pk": self.admin.pk},
        )
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_non_superuser_cannot_edit_superuser(self):
        boss = User.objects.create_superuser("boss", password="pw")
        self.client.login(username="admin", password="pw")
        self.client.post(
            reverse("crew_hub:user_admin"),
            {"action": "level", "pk": boss.pk, "level": roles.LEVEL_MEMBER},
        )
        boss.refresh_from_db()
        self.assertTrue(boss.is_superuser)
        self.assertEqual(roles.get_level(boss), roles.LEVEL_ADMIN)

    def test_password_reset_generates_temp_password(self):
        self.client.login(username="admin", password="pw")
        response = self.client.post(
            reverse("crew_hub:user_admin"),
            {"action": "password", "pk": self.member.pk},
            follow=True,
        )
        self.assertContains(response, "Temporary password:")
        self.member.refresh_from_db()
        self.assertFalse(self.member.check_password("pw"))


class RosterLoginCreationTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", roles.LEVEL_ADMIN)
        self.manager = make_user("manager", roles.LEVEL_MANAGER)

    def _add(self, extra=None):
        data = {
            "action": "add",
            "name": "Comms Test-Alpha",
            "username": "ctalpha",
            "email": "cta@example.org",
        }
        data.update(extra or {})
        return self.client.post(reverse("crew_hub:comm_staff"), data, follow=True)

    def test_admin_adds_person_with_login_and_level(self):
        self.client.login(username="admin", password="pw")
        response = self._add({"level": roles.LEVEL_REVIEWER})
        person = CommStaffMember.objects.get(name="Comms Test-Alpha")
        self.assertIsNotNone(person.user)
        self.assertEqual(person.user.get_username(), "ctalpha")
        self.assertEqual(person.user.first_name, "Comms")
        self.assertEqual(roles.get_level(person.user), roles.LEVEL_REVIEWER)
        self.assertContains(response, "Temporary password:")

    def test_manager_created_logins_are_members(self):
        self.client.login(username="manager", password="pw")
        self._add({"level": roles.LEVEL_MANAGER})
        person = CommStaffMember.objects.get(name="Comms Test-Alpha")
        self.assertEqual(roles.get_level(person.user), roles.LEVEL_MEMBER)

    def test_duplicate_username_keeps_person_without_login(self):
        make_user("ctalpha")
        self.client.login(username="admin", password="pw")
        response = self._add()
        person = CommStaffMember.objects.get(name="Comms Test-Alpha")
        self.assertIsNone(person.user)
        self.assertContains(response, "already exists")

    def test_add_without_username_creates_no_login(self):
        self.client.login(username="admin", password="pw")
        count = User.objects.count()
        self.client.post(
            reverse("crew_hub:comm_staff"),
            {"action": "add", "name": "Comms Test-Bravo", "username": ""},
        )
        self.assertTrue(
            CommStaffMember.objects.filter(name="Comms Test-Bravo").exists()
        )
        self.assertEqual(User.objects.count(), count)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="aoc-report@example.org",
)
class SendTestEmailCommandTests(TestCase):
    def test_sends_to_recipient(self):
        call_command("send_test_email", "dest@example.org")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["dest@example.org"])
        self.assertIn("test email", mail.outbox[0].subject.lower())


class MigrationGroupTests(TestCase):
    def test_level_groups_exist(self):
        for name in roles.LEVEL_GROUPS.values():
            self.assertTrue(Group.objects.filter(name=name).exists(), name)
