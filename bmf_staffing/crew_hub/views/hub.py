"""Crew Hub landing page: today at a glance."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .. import shifts
from ..models import CommShiftAssignment, DailyReport, DutyAssignment, Vehicle
from ..weather import get_base_weather
from .helpers import local_today


@login_required
def hub_home(request):
    today = local_today()

    # Both schedulers let several people share a seat/role, so names are
    # joined per slot rather than one name overwriting another.
    duty_by_role: dict[str, list[str]] = {}
    for assignment in DutyAssignment.objects.filter(date=today).select_related(
        "officer"
    ):
        if assignment.name and assignment.role:
            duty_by_role.setdefault(assignment.role, []).append(assignment.name)
    duty_rows = [
        {"label": label, "names": " / ".join(duty_by_role.get(code, [])) or "—"}
        for code, label in shifts.DUTY_ROLE_CHOICES
    ]

    comm_by_seat: dict[str, list[str]] = {}
    for a in CommShiftAssignment.objects.filter(date=today).select_related("member"):
        if a.name and a.seat:
            comm_by_seat.setdefault(a.seat, []).append(a.name)
    comm_filled = len(comm_by_seat)
    comm_rows = [
        {
            "label": seat.label,
            "name": " / ".join(comm_by_seat.get(seat.code, [])) or "—",
        }
        for seat in shifts.COMM_SEATS
    ]

    Vehicle.ensure_fleet()
    vehicles = list(Vehicle.objects.filter(active=True))
    exceptions = [
        v
        for v in vehicles
        if "OOS" in v.current_status.upper() or "INIS" in v.current_status.upper()
    ]

    report = DailyReport.objects.filter(report_date=today).first()

    return render(
        request,
        "crew_hub/hub_home.html",
        {
            "today": today,
            "duty_rows": duty_rows,
            "comm_rows": comm_rows,
            "comm_filled": comm_filled,
            "comm_total": len(shifts.COMM_SEATS),
            "vehicle_exceptions": exceptions,
            "vehicle_count": len(vehicles),
            "report": report,
            "base_weather": get_base_weather(),
        },
    )
