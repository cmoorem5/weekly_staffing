"""Email rendering: template renders without errors and matches the data."""

import datetime as dt

from django.test import TestCase

from crew_hub.emailer import render_report_email
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
            "System Misses",
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
        self.assertIn("System Misses: 4", html)
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
