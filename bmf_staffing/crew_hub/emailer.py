"""
Render and send the AOC Daily Report email.

The HTML template mirrors the reference SharePoint form's email output;
premailer inlines the CSS for email-client compatibility. Recipients and
SMTP settings come entirely from environment variables (console backend by
default in local dev, so no mail leaves the machine).
"""

from __future__ import annotations

import logging
from email.mime.image import MIMEImage

from django.conf import settings
from django.core.mail import EmailMessage
from django.template.loader import render_to_string

from . import shifts
from .models import DailyReport, ReportAuditLog

logger = logging.getLogger(__name__)

LOGO_CONTENT_ID = "bmf_logo"
LOGO_PATH = settings.WEEKLY_STAFFING_ROOT / "assets" / "bmf_coastal_logo.png"


def build_report_context(report: DailyReport) -> dict:
    """Template context shared by the email, the live preview, and copy-HTML."""
    duty = {e.role: e for e in report.duty_entries.all()}
    comm = {e.seat: e for e in report.comm_entries.all()}
    crew = {}
    for entry in report.crew_entries.all():
        crew.setdefault((entry.base, entry.shift_code), []).append(entry)

    base_sections = []
    for base in shifts.BASE_ORDER:
        rows = []
        for shift in shifts.SHIFTS_BY_BASE[base]:
            rows.append(
                {
                    "shift": shift,
                    "entries": crew.get((base, shift.code), []),
                }
            )
        base_sections.append({"header": shifts.BASE_HEADERS[base], "rows": rows})

    comm_columns = [
        [
            {"seat": shifts.COMM_SEAT_INDEX[code], "entry": comm.get(code)}
            for code in column
        ]
        for column in shifts.COMM_SEAT_COLUMNS
    ]
    # Email layout pairs the two columns row by row (D|N, D-2|N-2, ...).
    comm_pairs = list(zip(*comm_columns))

    vehicles = {"RW": [], "GR": []}
    for entry in report.vehicle_entries.all():
        vehicles.setdefault(entry.category, []).append(entry)

    sick_late = [e for e in report.sick_late_entries.all() if e.text.strip()]
    sick = [e for e in sick_late if e.entry_type == e.TYPE_SICK]
    late = [e for e in sick_late if e.entry_type == e.TYPE_LATE]

    summary = getattr(report, "transport_summary", None)
    base_counts = list(report.transport_base_counts.all())
    base_count_order = {b: i for i, b in enumerate(shifts.TRANSPORT_BASE_ORDER)}
    base_counts.sort(key=lambda row: base_count_order.get(row.base, 99))
    completed_total = sum(row.total for row in base_counts)
    miss_counts = list(report.miss_counts.all())
    miss_total = sum(row.count for row in miss_counts)

    return {
        "report": report,
        "duty_roles": [
            {"code": code, "label": label, "entry": duty.get(code)}
            for code, label in shifts.DUTY_ROLE_CHOICES
        ],
        "base_sections": base_sections,
        "extras": list(report.extra_entries.all()),
        "comm_columns": comm_columns,
        "comm_pairs": comm_pairs,
        "vehicles_rw": vehicles.get("RW", []),
        "vehicles_gr": vehicles.get("GR", []),
        "sick_entries": sick,
        "late_entries": late,
        "summary": summary,
        "base_counts": base_counts,
        "completed_total": completed_total,
        "pending_transports": list(report.pending_transports.all()),
        "miss_counts": miss_counts,
        "miss_total": miss_total,
        "equipment_dashboard_url": settings.CREW_HUB_EQUIPMENT_DASHBOARD_URL,
        "outreach_note": (
            "Outreach events and visitor details are tracked in Protean — "
            "no entry here."
        ),
    }


def render_report_email(report: DailyReport) -> str:
    """Report email HTML with CSS inlined for email clients."""
    html = render_to_string(
        "crew_hub/email/aoc_daily_report.html", build_report_context(report)
    )
    try:
        from premailer import transform

        return transform(html, disable_validation=True)
    except ImportError:  # pragma: no cover - premailer is a listed dependency
        logger.warning("premailer not installed; sending email without inlining")
        return html


def send_report_email(report: DailyReport, user=None) -> tuple[bool, str]:
    """Send the report. Returns (ok, error_message); the report stays
    submitted either way — callers surface the error and offer copy-HTML."""
    recipients = settings.CREW_HUB_REPORT_RECIPIENTS
    if not recipients:
        error = (
            "No recipients configured. Set AOC_REPORT_RECIPIENTS in your .env "
            "(comma-separated addresses)."
        )
        ReportAuditLog.objects.create(
            report=report,
            action=ReportAuditLog.ACTION_EMAIL_FAILED,
            actor=user,
            detail=error,
        )
        return False, error

    # Avoid %-d: not supported by Windows strftime.
    date = report.report_date
    subject = f"BMF AOC Daily Report — {date:%A, %B} {date.day}, {date.year}"
    message = EmailMessage(
        subject=subject,
        body=render_report_email(report),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=recipients,
    )
    message.content_subtype = "html"
    if LOGO_PATH.exists():
        logo = MIMEImage(LOGO_PATH.read_bytes())
        logo.add_header("Content-ID", f"<{LOGO_CONTENT_ID}>")
        logo.add_header("Content-Disposition", "inline", filename=LOGO_PATH.name)
        message.attach(logo)
    else:
        logger.warning("BMF logo not found at %s; sending report without it", LOGO_PATH)
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - surface any backend failure
        logger.exception("AOC report email failed for %s", report.report_date)
        ReportAuditLog.objects.create(
            report=report,
            action=ReportAuditLog.ACTION_EMAIL_FAILED,
            actor=user,
            detail=str(exc),
        )
        return False, str(exc)

    ReportAuditLog.objects.create(
        report=report,
        action=ReportAuditLog.ACTION_EMAIL_SENT,
        actor=user,
        detail=", ".join(recipients),
    )
    return True, ""
