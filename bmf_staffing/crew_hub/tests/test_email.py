"""Email rendering: template renders without errors and matches the data."""

import datetime as dt

from django.core import mail
from django.test import TestCase, override_settings

from crew_hub.emailer import LOGO_CONTENT_ID, render_report_email, send_report_email
from crew_hub.models import MissCategoryCount, PendingTransport
from crew_hub.services import get_or_create_report

DATE = dt.date(2026, 7, 2)


class EmailRenderTests(TestCase):
    def setUp(self):
        self.report, _ = get_or_create_report(DATE)

    def test_blank_report_renders(self):
        html = render_report_email(self.report)
        for section in (
            "Daily AOC Report",
            "Field Staffing",
            "Comm Center",
            "Vehicle Status",
            "Sick Calls",
            "Outreach",
            "Completed by Base",
            "System Activity",
            "Complex / Complicated Logistical Calls",
        ):
            self.assertIn(section, html, section)

    def test_ref_flag_renders_open_in_red(self):
        entry = self.report.crew_entries.get(
            base="PYM", shift_code="PG", position="EMT"
        )
        entry.ref_flag = True
        entry.save()
        html = render_report_email(self.report)
        self.assertIn("OPEN (EMT)", html)
        self.assertIn("#C12126", html)

    def test_names_and_transports_render(self):
        rn = self.report.crew_entries.get(base="BED", shift_code="D7B", position="RN")
        rn.name = "RN Test-Alpha"
        rn.save()
        summary = self.report.transport_summary
        summary.pending_count = 2
        summary.save()
        PendingTransport.objects.create(
            report=self.report,
            order=1,
            call_type="CCT",
            asset="N142NE",
            status="En route",
            location="Test Hospital",
        )
        row = self.report.transport_base_counts.get(base="BED")
        row.gcct, row.rw = 3, 2
        row.save()
        MissCategoryCount.objects.filter(report=self.report, label="Weather").update(
            count=4
        )

        html = render_report_email(self.report)
        self.assertIn("RN Test-Alpha", html)
        self.assertIn("Pending: 2", html)
        self.assertIn("Completed: 5", html)
        self.assertIn("System Activity: 4", html)
        self.assertIn("Test Hospital", html)

    def test_vehicle_status_coloring(self):
        entry = self.report.vehicle_entries.get(vehicle_id="N141NE")
        entry.status = "BED OOS - Maintenance"
        entry.save()
        inis = self.report.vehicle_entries.get(vehicle_id="Med 11")
        inis.status = "INIS - Needs equipment"
        inis.save()
        ok = self.report.vehicle_entries.get(vehicle_id="Med 12")
        ok.status = "PYM Primary"
        ok.save()
        html = render_report_email(self.report)
        self.assertIn("#C12126", html)  # OOS red
        self.assertIn("#B85C00", html)  # INIS orange
        self.assertIn("#0F6E56", html)  # Primary green

    def test_css_is_inlined_for_email_clients(self):
        html = render_report_email(self.report)
        self.assertIn('style="', html)
        self.assertNotIn("<link", html)

    def test_header_references_embedded_logo(self):
        html = render_report_email(self.report)
        self.assertIn(f"cid:{LOGO_CONTENT_ID}", html)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CREW_HUB_REPORT_RECIPIENTS=["ops-test@example.org"],
)
class EmailLogoAttachmentTests(TestCase):
    def setUp(self):
        self.report, _ = get_or_create_report(DATE)

    def test_logo_is_attached_inline(self):
        ok, error = send_report_email(self.report)
        self.assertTrue(ok, error)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        # EmailMessage.attach() with a MIMEImage stores the raw MIMEBase part
        # directly in .attachments rather than the (filename, content, mimetype)
        # tuple form used for plain attachments.
        self.assertTrue(
            any(
                a.get("Content-ID") == f"<{LOGO_CONTENT_ID}>"
                for a in sent.attachments
                if hasattr(a, "get")
            )
        )
