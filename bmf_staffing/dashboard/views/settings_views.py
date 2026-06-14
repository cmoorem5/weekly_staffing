"""In-app settings: roster, KPI thresholds, and admin tools hub."""

from django.contrib import messages
from django.db import IntegrityError
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.models import KpiThreshold
from staffing_tool.models import StaffRosterEntry as SaStaffRosterEntry
from staffing_tool.staff_roster import (
    add_roster_entries,
    canonical_display,
    list_roster_import_weeks,
    parse_roster_import_form_key,
    suggest_roster_imports,
)

from ..forms import (
    PERCENT_KPI_METRICS,
    KpiThresholdFormSet,
    ManagerRosterAddForm,
    StaffRosterAddForm,
)
from ..models import ManagerRosterLastName, StaffRosterEntry
from .helpers import (
    DB_PATH,
    FY_AND_PAY_PERIOD_POLICY_NOTE,
    _ensure_db,
    _utc_now_iso,
    staffing_db_health,
)


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

    row = session.query(KpiThreshold).filter(KpiThreshold.metric_name == metric).first()
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
    staff_roster_count = 0
    threshold_count = 0
    if DB_PATH:
        roster_count = ManagerRosterLastName.objects.using("staffing").count()
        staff_roster_count = (
            StaffRosterEntry.objects.using("staffing").filter(active=True).count()
        )
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
            "title": "Staff roster (RN / Medic / EMT)",
            "description": (
                "Clinical staff on imported schedules. Auto-updated on each "
                "schedule import; deactivate here to stop auto-add."
            ),
            "url_name": "staff_roster_settings",
            "meta": (
                f"{staff_roster_count} active"
                if staff_roster_count
                else "auto-fills on import"
            ),
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

    roster = list(ManagerRosterLastName.objects.using("staffing").order_by("last_name"))
    return render(
        request,
        "dashboard/manager_roster.html",
        {"roster": roster, "add_form": add_form},
    )


def _staff_roster_import_context(week_start: str | None) -> dict[str, object]:
    """Week list and import suggestions for the staff roster page."""
    if not DB_PATH:
        return {
            "import_weeks": [],
            "selected_import_week": "",
            "import_suggestions": [],
            "import_suggestions_by_role": {"RN": [], "MEDIC": [], "EMT": []},
        }
    with session_scope(DB_PATH) as session:
        weeks = list_roster_import_weeks(session)
        selected = week_start or (weeks[0] if weeks else "")
        suggestions = suggest_roster_imports(session, selected) if selected else []
    by_role: dict[str, list[dict[str, object]]] = {
        "RN": [],
        "MEDIC": [],
        "EMT": [],
    }
    for item in suggestions:
        by_role.setdefault(item.role, []).append(
            {
                "form_key": item.form_key,
                "display": item.display,
                "shift_count": item.shift_count,
            }
        )
    return {
        "import_weeks": weeks,
        "selected_import_week": selected,
        "import_suggestions": suggestions,
        "import_suggestions_by_role": by_role,
    }


def staff_roster_settings(request):
    """Add, deactivate, or reactivate RN / Medic / EMT staff roster entries."""
    _ensure_db()
    add_form = StaffRosterAddForm()
    import_week = (request.GET.get("import_week") or "").strip()

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "import_names":
            import_week = (request.POST.get("import_week") or "").strip()
            import_all = (request.POST.get("import_all") or "").strip() == "1"
            entries: list[tuple[str, str, str]] = []
            if import_all and DB_PATH and import_week:
                with session_scope(DB_PATH) as session:
                    for item in suggest_roster_imports(session, import_week):
                        entries.append((item.role, item.last_name, item.first_name))
            else:
                for key in request.POST.getlist("import_key"):
                    parsed = parse_roster_import_form_key(key)
                    if parsed:
                        entries.append(parsed)
            if not entries:
                messages.warning(
                    request,
                    "No people selected to add. Choose names from the import list.",
                )
            elif not DB_PATH:
                messages.error(request, "Database is not configured.")
            else:
                with session_scope(DB_PATH) as session:
                    added, skipped = add_roster_entries(
                        session,
                        entries,
                        created_at=_utc_now_iso(),
                    )
                if added:
                    messages.success(
                        request,
                        f"Added {added} name{'s' if added != 1 else ''} to the staff roster.",
                    )
                if skipped and not added:
                    messages.warning(
                        request,
                        "Those names are already on the roster.",
                    )
                elif skipped:
                    messages.info(
                        request,
                        f"Skipped {skipped} duplicate name{'s' if skipped != 1 else ''}.",
                    )
            return redirect(
                f"{request.path}?import_week={import_week}"
                if import_week
                else request.path
            )
        if action == "add":
            add_form = StaffRosterAddForm(request.POST)
            if add_form.is_valid():
                last_name = add_form.cleaned_data["last_name"]
                first_name = add_form.cleaned_data["first_name"]
                role = add_form.cleaned_data["role"]
                try:
                    StaffRosterEntry.objects.using("staffing").create(
                        last_name=last_name,
                        first_name=first_name,
                        role=role,
                        active=True,
                        created_at=_utc_now_iso(),
                    )
                    label = f"{last_name}, {first_name}" if first_name else last_name
                    messages.success(
                        request, f"Added {label} ({role}) to the staff roster."
                    )
                except IntegrityError:
                    messages.error(
                        request,
                        f"{last_name} is already on the {role} roster.",
                    )
                return redirect("staff_roster_settings")
        elif action == "deactivate":
            raw_id = (request.POST.get("roster_id") or "").strip()
            if raw_id.isdigit() and DB_PATH:
                entry_id = int(raw_id)
                label = ""
                role = ""
                with session_scope(DB_PATH) as session:
                    row = (
                        session.query(SaStaffRosterEntry)
                        .filter(
                            SaStaffRosterEntry.id == entry_id,
                            SaStaffRosterEntry.active == 1,
                        )
                        .first()
                    )
                    if row:
                        label = canonical_display(row)
                        role = row.role
                        row.active = 0
                if label:
                    messages.success(
                        request,
                        f"Removed {label} ({role}) from the active roster.",
                    )
                else:
                    messages.error(request, "Entry not found or already inactive.")
            elif raw_id.isdigit():
                messages.error(request, "Database is not configured.")
            else:
                messages.error(request, "Invalid roster entry.")
            return redirect("staff_roster_settings")
        elif action == "reactivate":
            raw_id = (request.POST.get("roster_id") or "").strip()
            if raw_id.isdigit() and DB_PATH:
                with session_scope(DB_PATH) as session:
                    updated = (
                        session.query(SaStaffRosterEntry)
                        .filter(
                            SaStaffRosterEntry.id == int(raw_id),
                            SaStaffRosterEntry.active == 0,
                        )
                        .update({SaStaffRosterEntry.active: 1})
                    )
                if updated:
                    messages.success(request, "Reactivated roster entry.")
                else:
                    messages.error(request, "Entry not found.")
            elif raw_id.isdigit():
                messages.error(request, "Database is not configured.")
            return redirect("staff_roster_settings")

    active_rows = list(
        StaffRosterEntry.objects.using("staffing")
        .filter(active=True)
        .order_by("role", "last_name", "first_name")
    )
    inactive_rows = list(
        StaffRosterEntry.objects.using("staffing")
        .filter(active=False)
        .order_by("role", "last_name", "first_name")
    )
    roster_by_role: dict[str, list[dict[str, object]]] = {
        "RN": [],
        "MEDIC": [],
        "EMT": [],
    }
    for row in active_rows:
        roster_by_role.setdefault(row.role, []).append(
            {
                "id": row.id,
                "role": row.role,
                "display": canonical_display(row),
                "last_name": row.last_name,
                "first_name": row.first_name,
            }
        )

    ctx = {
        "add_form": add_form,
        "roster_by_role": roster_by_role,
        "inactive_rows": inactive_rows,
        "active_count": len(active_rows),
    }
    ctx.update(_staff_roster_import_context(import_week or None))
    return render(request, "dashboard/staff_roster.html", ctx)


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
            rows = session.query(KpiThreshold).order_by(KpiThreshold.metric_name).all()
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
