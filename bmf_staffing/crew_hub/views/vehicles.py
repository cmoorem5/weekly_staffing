"""Live vehicle status board."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from ..models import Vehicle, VehicleStatusLog


@login_required
def vehicle_board(request):
    Vehicle.ensure_fleet()
    vehicles = list(Vehicle.objects.filter(active=True))

    if request.method == "POST":
        changed = 0
        with transaction.atomic():
            for vehicle in vehicles:
                new_status = request.POST.get(f"status_{vehicle.pk}", "").strip()
                if new_status != vehicle.current_status:
                    vehicle.current_status = new_status
                    vehicle.save(update_fields=["current_status", "updated_at"])
                    VehicleStatusLog.objects.create(
                        vehicle=vehicle, status=new_status, changed_by=request.user
                    )
                    changed += 1
        if changed:
            messages.success(request, f"Updated {changed} vehicle status(es).")
        else:
            messages.info(request, "No vehicle status changes.")
        return redirect("crew_hub:vehicle_board")

    recent_logs = VehicleStatusLog.objects.select_related(
        "vehicle", "changed_by"
    )[:15]
    return render(
        request,
        "crew_hub/vehicle_board.html",
        {
            "rotor": [v for v in vehicles if v.category == "RW"],
            "ground": [v for v in vehicles if v.category == "GR"],
            "recent_logs": recent_logs,
        },
    )
