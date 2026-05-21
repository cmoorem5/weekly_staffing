"""In-app settings: roster, KPI thresholds, and admin tools hub."""

from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.models import KpiThreshold

from ..forms import (
    KpiThresholdFormSet,
    ManagerRosterAddForm,
    PERCENT_KPI_METRICS,
)
from ..models import ManagerRosterLastName
from .helpers import DB_PATH, FY_AND_PAY_PERIOD_POLICY_NOTE, _ensure_db, staffing_db_health


def _threshold_to_form_initial(row: KpiThreshold) -> dict[str, object]:
    """Convert DB row to form initial values (percent metrics shown as 0–100)."""
    is_pct = row.metric_name in PERCENT_KPI_METRICS
    scale = 100.0 if is_pct else 1.0

    def _v(val: float | None) -> float | None:
        if val is None:
            return None
        return round(val * scale, 4)

    return {
        "metric_name": row.metric_name,
        "green_min": _v(row.green_min),
        "green_max": _v(row.green_max),
        "yellow_min": _v(row.yellow_min),
        "yellow_max": _v(row.yellow_max),
        "red_min": _v(row.red_min),
        "red_max": _v(row.red_max),
        "higher_is_better": bool(row.higher_is_better),
        "is_percent": is_pct,
    }


def _save_threshold_from_form(form, session) -> None:
    metric = form.cleaned_data["metric_name"]
    is_pct = metric in PERCENT_KPI_METRICS
    scale = 100.0 if is_pct else 1.0

    def _store(key: str) -> float | None:
        val = form.cleaned_data.get(key)
        if val is None or val == "":
            return None
        return float(val) / scale

    row = (
        session.query(KpiThreshold)
        .filter(KpiThreshold.metric_name == metric)
        .first()
    )
    if not row:
        row = KpiThreshold(metric_name=metric)
        session.add(row)
    row.green_min = _store("green_min")
    row.green_max = _store("green_max")
    row.yellow_min = _store("yellow_min")
    row.yellow_max = _store("yellow_max")
    row.red_min = _store("red_min")
    row.red_max = _store("red_max")
    row.higher_is_better = 1 if form.cleaned_data.get("higher_is_better") else 0


def settings_index(request):
    """Settings hub — configuration and database tools."""
    _ensure_db()
    roster_count = 0
    threshold_count = 0
    if DB_PATH:
        roster_count = ManagerRosterLastName.objects.using("staffing").count()
        with session_scope(DB_PATH) as session:
            threshold_count = session.query(KpiThreshold).count()

    setting_cards = [
        {
            "title": "Manager roster",
            "description": (
                "Last names that identify manager rows on imported schedules. "
                "Used for leave exclusion and manager line-shift tracking."
            ),
            "url_name": "manager_roster_settings",
            "meta": f"{roster_count} name{'s' if roster_count != 1 else ''}",
        },
        {
            "title": "KPI thresholds",
            "description": (
                "Green / yellow / red ranges for dashboard RAG status and board pack targets."
            ),
            "url_name": "kpi_thresholds_settings",
            "meta": f"{threshold_count} metric{'s' if threshold_count != 1 else ''}",
        },
        {
            "title": "Base totals (RW / GR)",
            "description": (
                "Weekly RW and GR unit-day denominators per base. Set once; edit if operational capacity changes."
            ),
            "url_name": "base_totals",
            "meta": "5 bases",
        },
        {
            "title": "Backup database",
            "description": "Copy staffing.db to archive/ with a timestamp (localhost only).",
            "url_name": "backup_db",
            "meta": "Admin tool",
        },
        {
            "title": "Restore database",
            "description": "Restore staffing.db from a backup in archive/ (localhost only).",
            "url_name": "restore_db",
            "meta": "Admin tool",
        },
    ]

    return render(
        request,
        "dashboard/settings.html",
        {
            "setting_cards": setting_cards,
            "fy_policy_note": FY_AND_PAY_PERIOD_POLICY_NOTE,
            "db_health": staffing_db_health(DB_PATH),
        },
    )


def manager_roster_settings(request):
    """Add or remove manager last names (schedule import roster)."""
    _ensure_db()
    add_form = ManagerRosterAddForm()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "add":
            add_form = ManagerRosterAddForm(request.POST)
            if add_form.is_valid():
                name = add_form.cleaned_data["last_name"].strip()
                try:
                    ManagerRosterLastName.objects.using("staffing").create(
                        last_name=name
                    )
                    messages.success(request, f"Added {name} to the manager roster.")
                except IntegrityError:
                    messages.error(request, f"{name} is already on the roster.")
                return redirect("manager_roster_settings")
        elif action == "delete":
            raw_id = (request.POST.get("roster_id") or "").strip()
            if raw_id.isdigit():
                deleted, _ = (
                    ManagerRosterLastName.objects.using("staffing")
                    .filter(id=int(raw_id))
                    .delete()
                )
                if deleted:
                    messages.success(request, "Removed name from roster.")
                else:
                    messages.error(request, "Name not found.")
            return redirect("manager_roster_settings")

    roster = list(
        ManagerRosterLastName.objects.using("staffing").order_by("last_name")
    )
    return render(
        request,
        "dashboard/manager_roster.html",
        {"roster": roster, "add_form": add_form},
    )


def kpi_thresholds_settings(request):
    """Edit KPI RAG threshold ranges."""
    _ensure_db()
    if not DB_PATH:
        messages.error(request, "Database is not configured.")
        return redirect("settings_index")

    if request.method == "POST":
        formset = KpiThresholdFormSet(request.POST)
        if formset.is_valid():
            try:
                with session_scope(DB_PATH) as session:
                    for form in formset:
                        if form.cleaned_data.get("metric_name"):
                            _save_threshold_from_form(form, session)
                messages.success(request, "KPI thresholds saved.")
                return redirect("kpi_thresholds_settings")
            except Exception as exc:
                messages.error(request, str(exc))
    else:
        with session_scope(DB_PATH) as session:
            rows = (
                session.query(KpiThreshold)
                .order_by(KpiThreshold.metric_name)
                .all()
            )
        initial = [_threshold_to_form_initial(r) for r in rows]
        formset = KpiThresholdFormSet(initial=initial)

    metric_rows = []
    for form in formset:
        metric_rows.append(
            {
                "form": form,
                "is_percent": form.initial.get("is_percent")
                or form["metric_name"].value() in PERCENT_KPI_METRICS,
            }
        )

    return render(
        request,
        "dashboard/kpi_thresholds.html",
        {"formset": formset, "metric_rows": metric_rows},
    )
