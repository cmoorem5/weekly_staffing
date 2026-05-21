"""Schedule import view."""

import os
from datetime import UTC, datetime

from django.contrib import messages
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.models import (
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyManagerShift,
    WeeklyStaffing,
)
from staffing_tool.schedule_import import (
    AggregatedWeek,
    aggregate_week_from_records,
    detect_schedule_week_starts,
    parse_schedule_workbook,
    weekly_manager_shift_mappings,
)

from .helpers import (
    _SCHEDULE_UPLOAD_PREFIX,
    BASES,
    DB_PATH,
    _agg_leave_total,
    _cleanup_old_schedule_uploads,
    _ensure_db,
    _last_sunday,
    _manager_last_names_upper_for_parse,
    _ops_coverage_total,
    _schedule_upload_dir,
    _utc_now_iso,
)


def _build_import_preview_context(
    upload_path: str, week_start_hint: str
) -> dict | None:
    """Context for import preview, or None if no week headers were found in the file."""
    detected = detect_schedule_week_starts(upload_path)
    if not detected:
        return None
    ws_show = week_start_hint if week_start_hint in detected else detected[0]
    mgr_names = _manager_last_names_upper_for_parse()
    records, issues, _ = parse_schedule_workbook(
        upload_path,
        week_start=ws_show,
        manager_last_names_upper=mgr_names,
    )
    filled_count = sum(1 for r in records if r.filled)
    ot_count = sum(1 for r in records if r.overtime and r.filled)
    leave_count = sum(1 for r in records if r.leave_type)
    manager_line_count = sum(
        1
        for r in records
        if r.filled
        and r.is_manager_row
        and r.role in {"RN", "MEDIC", "EMT"}
        and (r.base or "").strip()
    )
    unknown_units = [i for i in issues if i.issue_type == "unknown_unit"]
    return {
        "week_start": ws_show,
        "detected_weeks": detected,
        "upload_path": upload_path,
        "records_count": len(records),
        "filled_count": filled_count,
        "ot_count": ot_count,
        "leave_count": leave_count,
        "manager_line_count": manager_line_count,
        "issues": issues,
        "unknown_units": unknown_units,
    }
def import_schedule(request):
    """Upload a schedule workbook, preview parsed results, and optionally apply to create/update a week."""
    _ensure_db()
    _cleanup_old_schedule_uploads(_schedule_upload_dir())

    if request.method == "POST":
        week_start = (request.POST.get("week_start") or _last_sunday()).strip()

        # Re-parse preview after changing which week is selected (same uploaded file).
        if request.POST.get("action") == "update_preview" and request.POST.get(
            "upload_path"
        ):
            upload_path = request.POST.get("upload_path", "")
            if not upload_path or not os.path.isfile(upload_path):
                messages.error(
                    request,
                    "Uploaded file not found on server; please upload again.",
                )
                return redirect("import_schedule")
            week_start = (request.POST.get("week_start") or "").strip()
            ctx = _build_import_preview_context(upload_path, week_start)
            if ctx is None:
                messages.error(
                    request,
                    "Could not read dates from row 1 (columns C–P) on RN & Medic or EMT.",
                )
                return redirect("import_schedule")
            return render(request, "dashboard/import_schedule_preview.html", ctx)

        # Step 2: Apply existing uploaded file to create/update week.
        if request.POST.get("action") == "apply" and request.POST.get("upload_path"):
            upload_path = request.POST.get("upload_path")
            if not os.path.isfile(upload_path):
                messages.error(
                    request, "Uploaded file not found on server; please upload again."
                )
                return redirect("import_schedule")

            detected_apply = detect_schedule_week_starts(upload_path)
            if detected_apply and week_start not in detected_apply:
                messages.error(
                    request,
                    f"Week {week_start} is not in this file. "
                    f"Detected weeks: {', '.join(detected_apply)}.",
                )
                return redirect("import_schedule")

            # Build unit_overrides from mapping form: raw_X -> map_X
            unit_overrides = {}
            for key, map_to in request.POST.items():
                if key.startswith("map_") and map_to and map_to.strip():
                    raw_key = "raw_" + key[4:]
                    raw_val = request.POST.get(raw_key)
                    if raw_val:
                        unit_overrides[raw_val.strip().upper()] = map_to.strip().upper()

            try:
                mgr_names = _manager_last_names_upper_for_parse()
                records, issues, ops_coverage = parse_schedule_workbook(
                    upload_path,
                    week_start=week_start,
                    unit_overrides=unit_overrides,
                    manager_last_names_upper=mgr_names,
                )
            except Exception as exc:
                messages.error(request, f"Error parsing workbook: {exc}")
                return redirect("import_schedule")

            if not records:
                mismatch = next(
                    (i for i in issues if i.issue_type == "week_mismatch"),
                    None,
                )
                if mismatch:
                    messages.error(request, mismatch.message)
                else:
                    messages.error(
                        request,
                        "No usable shifts found in schedule file.",
                    )
                return redirect("import_schedule")

            # Aggregate into weekly metrics and base coverage (OPS View used for RW/GR when available).
            agg: AggregatedWeek = aggregate_week_from_records(
                week_start, records, ops_coverage=ops_coverage
            )

            if (
                agg.filled_day == 0
                and agg.filled_night == 0
                and _agg_leave_total(agg) == 0
                and _ops_coverage_total(ops_coverage) == 0
            ):
                dates = sorted({r.date for r in records})
                if dates:
                    span = f"{dates[0].isoformat()} through {dates[-1].isoformat()}"
                else:
                    span = "(none)"
                messages.error(
                    request,
                    "Import was not saved: it produced no crew shifts (RN+Medic pairs), no "
                    "schedule exceptions, and no OPS View base coverage for the week you "
                    f"selected ({week_start}). Parsed cells only covered dates {span}. "
                    "A schedule labeled 29 Mar 2026 is usually week start 2026-03-29 "
                    "(Sun–Sat Mar 29–Apr 4), not 2026-04-05. Use 2026-04-05 only if the "
                    "Excel date row includes Apr 5–11.",
                )
                return redirect("import_schedule")

            ot_total = (
                agg.ot_rn_day
                + agg.ot_rn_night
                + agg.ot_medic_day
                + agg.ot_medic_night
                + agg.ot_emt_day
                + agg.ot_emt_night
            )

            now = _utc_now_iso()
            with session_scope(DB_PATH) as session:
                # Upsert WeeklyStaffing.
                row = (
                    session.query(WeeklyStaffing)
                    .filter(WeeklyStaffing.week_start == week_start)
                    .first()
                )
                notes = "Imported from schedule"
                if row:
                    row.filled_day = agg.filled_day
                    row.filled_night = agg.filled_night
                    row.ot_shifts = ot_total
                    row.ot_rn = agg.ot_rn_day + agg.ot_rn_night
                    row.ot_medic = agg.ot_medic_day + agg.ot_medic_night
                    row.ot_emt = agg.ot_emt_day + agg.ot_emt_night
                    row.ot_rn_day = agg.ot_rn_day
                    row.ot_rn_night = agg.ot_rn_night
                    row.ot_medic_day = agg.ot_medic_day
                    row.ot_medic_night = agg.ot_medic_night
                    row.ot_emt_day = agg.ot_emt_day
                    row.ot_emt_night = agg.ot_emt_night
                    row.leave_at = agg.leave_at
                    row.leave_lt = agg.leave_lt
                    row.leave_sick = agg.leave_sick
                    row.leave_loa = agg.leave_loa
                    row.leave_jury = getattr(agg, "leave_jury", 0)
                    row.leave_brev = getattr(agg, "leave_brev", 0)
                    row.notes = notes
                    row.updated_at = now
                else:
                    session.add(
                        WeeklyStaffing(
                            week_start=week_start,
                            day_target=8,
                            night_min=4,
                            filled_day=agg.filled_day,
                            filled_night=agg.filled_night,
                            ot_shifts=ot_total,
                            ot_rn=agg.ot_rn_day + agg.ot_rn_night,
                            ot_medic=agg.ot_medic_day + agg.ot_medic_night,
                            ot_emt=agg.ot_emt_day + agg.ot_emt_night,
                            ot_rn_day=agg.ot_rn_day,
                            ot_rn_night=agg.ot_rn_night,
                            ot_medic_day=agg.ot_medic_day,
                            ot_medic_night=agg.ot_medic_night,
                            ot_emt_day=agg.ot_emt_day,
                            ot_emt_night=agg.ot_emt_night,
                            leave_at=agg.leave_at,
                            leave_lt=agg.leave_lt,
                            leave_sick=agg.leave_sick,
                            leave_loa=agg.leave_loa,
                            leave_jury=getattr(agg, "leave_jury", 0),
                            leave_brev=getattr(agg, "leave_brev", 0),
                            overnights_below=0,
                            pilot_vacancies=0,
                            notes=notes,
                            entered_by=request.user.username
                            if request.user.is_authenticated
                            else "import",
                            created_at=now,
                            updated_at=now,
                        )
                    )

                # Ensure ``weekly_staffing`` row exists in SQLite before child FK inserts
                # (``bulk_insert_mappings`` for manager shifts does not trigger an autoflush).
                session.flush()

                # Upsert leave breakdown (columns = leave types, rows = RN, Medic, EMT, Pilot).
                session.query(WeeklyLeaveDetail).filter(
                    WeeklyLeaveDetail.week_start == week_start
                ).delete()
                for (role, leave_type), count in getattr(
                    agg, "leave_breakdown", {}
                ).items():
                    if count:
                        session.add(
                            WeeklyLeaveDetail(
                                week_start=week_start,
                                role=role,
                                leave_type=leave_type,
                                count=count,
                            )
                        )

                session.query(WeeklyManagerShift).filter(
                    WeeklyManagerShift.week_start == week_start
                ).delete()
                mgr_maps = weekly_manager_shift_mappings(week_start, records)
                if mgr_maps:
                    session.bulk_insert_mappings(WeeklyManagerShift, mgr_maps)

                # Upsert WeeklyBaseCoverage for all known bases.
                for base_name in BASES:
                    rw_d = agg.base_rw_staffed_day.get(base_name, 0)
                    rw_n = agg.base_rw_staffed_night.get(base_name, 0)
                    gr_d = agg.base_gr_staffed_day.get(base_name, 0)
                    gr_n = agg.base_gr_staffed_night.get(base_name, 0)
                    rw_s = rw_d + rw_n
                    gr_s = gr_d + gr_n

                    rec = (
                        session.query(WeeklyBaseCoverage)
                        .filter(
                            WeeklyBaseCoverage.week_start == week_start,
                            WeeklyBaseCoverage.base_name == base_name,
                        )
                        .first()
                    )
                    if rec:
                        rec.rw_staffed_unit_days = rw_s
                        rec.gr_staffed_unit_days = gr_s
                        rec.rw_staffed_day = rw_d
                        rec.rw_staffed_night = rw_n
                        rec.gr_staffed_day = gr_d
                        rec.gr_staffed_night = gr_n
                    else:
                        session.add(
                            WeeklyBaseCoverage(
                                week_start=week_start,
                                base_name=base_name,
                                rw_staffed_unit_days=rw_s,
                                gr_staffed_unit_days=gr_s,
                                rw_staffed_day=rw_d,
                                rw_staffed_night=rw_n,
                                gr_staffed_day=gr_d,
                                gr_staffed_night=gr_n,
                            )
                        )

            messages.success(
                request,
                f"Week {week_start} imported from schedule. Please review and export.",
            )
            return redirect("week_edit", week_start=week_start)

        # Step 1: initial upload -> preview.
        file = request.FILES.get("schedule_file")
        if not file:
            messages.error(request, "Please choose a schedule Excel file to upload.")
            return render(
                request,
                "dashboard/import_schedule.html",
                {
                    "week_start": week_start,
                },
            )

        upload_dir = _schedule_upload_dir()
        os.makedirs(upload_dir, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        filename = f"{_SCHEDULE_UPLOAD_PREFIX}{timestamp}.xlsx"
        saved_path = os.path.join(upload_dir, filename)
        with open(saved_path, "wb") as dest:
            for chunk in file.chunks():
                dest.write(chunk)

        request.session["schedule_upload_original_name"] = (file.name or "")[:500]
        request.session.modified = True

        ctx = _build_import_preview_context(saved_path, week_start)
        if ctx is None:
            messages.error(
                request,
                "Could not read dates from row 1 (columns C–P) on RN & Medic or EMT. "
                "Check the workbook layout.",
            )
            return render(
                request,
                "dashboard/import_schedule.html",
                {"week_start": week_start},
            )
        detected = ctx["detected_weeks"]
        if week_start not in detected:
            messages.info(
                request,
                f"This file contains week start(s): {', '.join(detected)}. "
                f"Preview below uses {ctx['week_start']}. Choose another week and "
                "click Update preview if needed, then Apply.",
            )
        return render(request, "dashboard/import_schedule_preview.html", ctx)

    return render(
        request,
        "dashboard/import_schedule.html",
        {
            "week_start": _last_sunday(),
        },
    )
