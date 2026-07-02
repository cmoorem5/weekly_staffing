"""AOC Daily Report: entry form, save/preview/submit/reopen, list, fallback."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .. import shifts
from ..emailer import build_report_context, render_report_email, send_report_email
from ..models import (
    DailyReport,
    ExtraEntry,
    MissCategoryCount,
    PendingTransport,
    SickLateEntry,
)
from ..services import (
    get_or_create_report,
    refresh_from_sources,
    reopen_report,
    submit_report,
)
from .helpers import local_today, parse_date_or_404


def _report_for(date_str: str) -> DailyReport:
    date = parse_date_or_404(date_str)
    report, _ = get_or_create_report(date)
    return report


def _int(value: str) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


@transaction.atomic
def _apply_post(report: DailyReport, post) -> None:
    """Persist the entry form. Drafts only; completeness is never enforced."""
    report.weather = post.get("weather", "").strip()
    report.save(update_fields=["weather", "updated_at"])

    for entry in report.duty_entries.all():
        entry.name = post.get(f"duty_{entry.role}", "").strip()
        entry.save(update_fields=["name"])

    for entry in report.crew_entries.all():
        entry.name = post.get(f"crew_{entry.pk}_name", "").strip()
        entry.ref_flag = f"crew_{entry.pk}_ref" in post
        entry.save(update_fields=["name", "ref_flag"])

    report.extra_entries.all().delete()
    extras = zip(
        post.getlist("extra_base"),
        post.getlist("extra_shift"),
        post.getlist("extra_role"),
        post.getlist("extra_name"),
        post.getlist("extra_note"),
    )
    for base, shift_code, role, name, note in extras:
        if any(v.strip() for v in (name, note)):
            ExtraEntry.objects.create(
                report=report,
                base=base.strip(),
                shift_code=shift_code.strip(),
                role=role.strip(),
                name=name.strip(),
                note=note.strip(),
            )

    for entry in report.comm_entries.all():
        entry.name = post.get(f"comm_{entry.seat}", "").strip()
        entry.save(update_fields=["name"])

    for entry in report.vehicle_entries.all():
        entry.status = post.get(f"vehicle_{entry.pk}", "").strip()
        entry.save(update_fields=["status"])

    report.sick_late_entries.all().delete()
    sick_text = post.get("sick_calls", "").strip()
    late_text = post.get("late_arrivals", "").strip()
    if sick_text:
        SickLateEntry.objects.create(
            report=report, entry_type=SickLateEntry.TYPE_SICK, text=sick_text
        )
    if late_text:
        SickLateEntry.objects.create(
            report=report, entry_type=SickLateEntry.TYPE_LATE, text=late_text
        )

    summary = report.transport_summary
    summary.pending_count = _int(post.get("pending_count"))
    summary.complex_calls = post.get("complex_calls", "").strip()
    summary.save(update_fields=["pending_count", "complex_calls"])

    for row in report.transport_base_counts.all():
        row.gcct = _int(post.get(f"tb_{row.base}_gcct"))
        row.rw = _int(post.get(f"tb_{row.base}_rw"))
        row.save(update_fields=["gcct", "rw"])

    report.pending_transports.all().delete()
    pending = zip(
        post.getlist("pt_call_type"),
        post.getlist("pt_asset"),
        post.getlist("pt_status"),
        post.getlist("pt_location"),
    )
    order = 0
    for call_type, asset, status, location in pending:
        if any(v.strip() for v in (call_type, asset, status, location)):
            order += 1
            PendingTransport.objects.create(
                report=report,
                order=order,
                call_type=call_type.strip(),
                asset=asset.strip(),
                status=status.strip(),
                location=location.strip(),
            )

    report.miss_counts.all().delete()
    for i, (label, count) in enumerate(
        zip(post.getlist("miss_label"), post.getlist("miss_count"))
    ):
        if label.strip() or _int(count):
            MissCategoryCount.objects.create(
                report=report, order=i, label=label.strip(), count=_int(count)
            )


def _form_context(report: DailyReport) -> dict:
    context = build_report_context(report)
    context.update(
        {
            "shift_choices": shifts.SHIFT_CHOICES,
            "base_choices": shifts.BASE_CHOICES,
            "position_choices": shifts.POSITION_CHOICES,
            "can_reopen": False,  # set per-request in report_detail
        }
    )
    return context


@login_required
def report_today(request):
    return redirect("crew_hub:report_detail", date_str=local_today().isoformat())


@login_required
def report_detail(request, date_str):
    report = _report_for(date_str)
    context = _form_context(report)
    context["can_reopen"] = request.user.has_perm("crew_hub.reopen_report")
    return render(request, "crew_hub/report_form.html", context)


@login_required
@require_POST
def report_save(request, date_str):
    report = _report_for(date_str)
    if report.is_submitted:
        messages.error(
            request,
            "This report has been submitted and is locked. "
            "Ask a supervisor to reopen it before editing.",
        )
    else:
        _apply_post(report, request.POST)
        messages.success(request, f"Draft saved for {report.report_date}.")
    return redirect("crew_hub:report_detail", date_str=date_str)


@login_required
@require_POST
def report_refresh(request, date_str):
    report = _report_for(date_str)
    if report.is_submitted:
        messages.error(request, "Submitted reports cannot be refreshed.")
    else:
        refresh_from_sources(report)
        messages.success(
            request,
            "Re-pulled duty officers, Comm Center, and vehicle statuses "
            "from the live schedules.",
        )
    return redirect("crew_hub:report_detail", date_str=date_str)


@login_required
def report_preview(request, date_str):
    """The report exactly as the email will look (same template + inlining)."""
    report = _report_for(date_str)
    return HttpResponse(render_report_email(report))


@login_required
def report_html(request, date_str):
    """Copy-HTML fallback so the report can be pasted into Outlook manually."""
    report = _report_for(date_str)
    return render(
        request,
        "crew_hub/report_copy_html.html",
        {"report": report, "email_html": render_report_email(report)},
    )


@login_required
@require_POST
def report_submit(request, date_str):
    report = _report_for(date_str)
    if report.is_submitted:
        messages.error(request, "This report was already submitted.")
        return redirect("crew_hub:report_detail", date_str=date_str)

    _apply_post(report, request.POST)
    submit_report(report, request.user)

    ok, error = send_report_email(report, request.user)
    if ok:
        messages.success(
            request,
            f"Report for {report.report_date} submitted and emailed.",
        )
    else:
        messages.error(
            request,
            "Report submitted, but the email failed to send: "
            f"{error} — use “Copy HTML” below to paste it into Outlook.",
        )
    return redirect("crew_hub:report_detail", date_str=date_str)


@login_required
@permission_required("crew_hub.reopen_report", raise_exception=True)
@require_POST
def report_reopen(request, date_str):
    report = _report_for(date_str)
    if not report.is_submitted:
        messages.info(request, "This report is not locked.")
    else:
        reopen_report(report, request.user)
        messages.success(
            request,
            f"Report for {report.report_date} reopened for editing "
            "(event logged).",
        )
    return redirect("crew_hub:report_detail", date_str=date_str)


@login_required
def report_list(request):
    reports = DailyReport.objects.select_related("submitted_by")[:120]
    return render(request, "crew_hub/report_list.html", {"reports": reports})
