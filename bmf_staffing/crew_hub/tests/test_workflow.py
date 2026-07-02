"""View workflow: auth, draft saves, submit locking, reopen permission."""

import datetime as dt

from django.contrib.auth.models import Permission, User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from crew_hub.models import DailyReport, ReportAuditLog
from crew_hub.services import get_or_create_report

DATE = dt.date(2026, 7, 2)
DATE_STR = "2026-07-02"


def detail_url():
    return reverse("crew_hub:report_detail", kwargs={"date_str": DATE_STR})


class WorkflowTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("aoc-test", password="pw")
        self.client.login(username="aoc-test", password="pw")


class AuthTests(WorkflowTestCase):
    def test_all_hub_views_require_login(self):
        self.client.logout()
        for url in (
            reverse("crew_hub:hub_home"),
            reverse("crew_hub:comm_month"),
            reverse("crew_hub:duty_month"),
            reverse("crew_hub:vehicle_board"),
            reverse("crew_hub:report_list"),
            detail_url(),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/accounts/login/", response["Location"], url)


class DraftSaveTests(WorkflowTestCase):
    def test_open_report_page_seeds_skeleton(self):
        response = self.client.get(detail_url())
        self.assertEqual(response.status_code, 200)
        report = DailyReport.objects.get(report_date=DATE)
        self.assertEqual(report.crew_entries.count(), 39)

    def test_save_persists_names_and_ref_flags(self):
        report, _ = get_or_create_report(DATE)
        rn = report.crew_entries.get(base="BED", shift_code="D7B", position="RN")
        pilot = report.crew_entries.get(base="BED", shift_code="D7B", position="PILOT")
        response = self.client.post(
            reverse("crew_hub:report_save", kwargs={"date_str": DATE_STR}),
            {
                f"crew_{rn.pk}_name": "RN Test-Alpha",
                f"crew_{pilot.pk}_ref": "on",
                "weather": "Sunny.",
                "pending_count": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        rn.refresh_from_db()
        pilot.refresh_from_db()
        report.refresh_from_db()
        self.assertEqual(rn.name, "RN Test-Alpha")
        self.assertTrue(pilot.ref_flag)
        self.assertFalse(rn.ref_flag)
        self.assertEqual(report.weather, "Sunny.")
        self.assertEqual(report.status, DailyReport.STATUS_DRAFT)

    def test_blank_names_allowed_on_draft(self):
        get_or_create_report(DATE)
        response = self.client.post(
            reverse("crew_hub:report_save", kwargs={"date_str": DATE_STR}), {}
        )
        self.assertEqual(response.status_code, 302)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CREW_HUB_REPORT_RECIPIENTS=["ops-test@example.org"],
)
class SubmitTests(WorkflowTestCase):
    def test_submit_locks_and_emails(self):
        get_or_create_report(DATE)
        response = self.client.post(
            reverse("crew_hub:report_submit", kwargs={"date_str": DATE_STR}), {}
        )
        self.assertEqual(response.status_code, 302)
        report = DailyReport.objects.get(report_date=DATE)
        self.assertTrue(report.is_submitted)
        self.assertEqual(report.submitted_by, self.user)
        self.assertIsNotNone(report.submitted_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("AOC Daily Report", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["ops-test@example.org"])
        actions = set(report.audit_logs.values_list("action", flat=True))
        self.assertIn(ReportAuditLog.ACTION_SUBMITTED, actions)
        self.assertIn(ReportAuditLog.ACTION_EMAIL_SENT, actions)

    def test_locked_report_rejects_saves(self):
        report, _ = get_or_create_report(DATE)
        self.client.post(
            reverse("crew_hub:report_submit", kwargs={"date_str": DATE_STR}), {}
        )
        rn = report.crew_entries.get(base="BED", shift_code="D7B", position="RN")
        self.client.post(
            reverse("crew_hub:report_save", kwargs={"date_str": DATE_STR}),
            {f"crew_{rn.pk}_name": "Should Not-Persist"},
        )
        rn.refresh_from_db()
        self.assertEqual(rn.name, "")

    def test_double_submit_rejected(self):
        get_or_create_report(DATE)
        url = reverse("crew_hub:report_submit", kwargs={"date_str": DATE_STR})
        self.client.post(url, {})
        self.client.post(url, {})
        report = DailyReport.objects.get(report_date=DATE)
        self.assertEqual(
            report.audit_logs.filter(action=ReportAuditLog.ACTION_SUBMITTED).count(),
            1,
        )
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(CREW_HUB_REPORT_RECIPIENTS=[])
    def test_send_failure_keeps_report_submitted(self):
        get_or_create_report(DATE)
        self.client.post(
            reverse("crew_hub:report_submit", kwargs={"date_str": DATE_STR}), {}
        )
        report = DailyReport.objects.get(report_date=DATE)
        self.assertTrue(report.is_submitted)
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(
            report.audit_logs.filter(action=ReportAuditLog.ACTION_EMAIL_FAILED).exists()
        )


class ReopenTests(WorkflowTestCase):
    def _submit(self):
        get_or_create_report(DATE)
        with override_settings(
            CREW_HUB_REPORT_RECIPIENTS=["ops-test@example.org"],
            EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        ):
            self.client.post(
                reverse("crew_hub:report_submit", kwargs={"date_str": DATE_STR}),
                {},
            )

    def test_reopen_requires_permission(self):
        self._submit()
        response = self.client.post(
            reverse("crew_hub:report_reopen", kwargs={"date_str": DATE_STR})
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(DailyReport.objects.get(report_date=DATE).is_submitted)

    def test_reopen_with_permission_unlocks_and_logs(self):
        self._submit()
        self.user.user_permissions.add(Permission.objects.get(codename="reopen_report"))
        self.client.login(username="aoc-test", password="pw")
        response = self.client.post(
            reverse("crew_hub:report_reopen", kwargs={"date_str": DATE_STR})
        )
        self.assertEqual(response.status_code, 302)
        report = DailyReport.objects.get(report_date=DATE)
        self.assertFalse(report.is_submitted)
        self.assertTrue(
            report.audit_logs.filter(
                action=ReportAuditLog.ACTION_REOPENED, actor=self.user
            ).exists()
        )
