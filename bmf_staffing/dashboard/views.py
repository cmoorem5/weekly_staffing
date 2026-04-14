"""
Dashboard views: use staffing_tool package and existing staffing.db.
"""

import os
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import cast

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render

# staffing_tool is on path via manage.py / WEEKLY_STAFFING_ROOT
from staffing_tool.db import init_db, session_scope
from staffing_tool.leave_grid import (
    EXCEPTION_COL_BREAKDOWN_KEYS,
    EXCEPTION_COL_DB_TYPE,
    EXCEPTION_GRID_COLS,
    EXCEPTION_GRID_ROLES,
    LEAVE_TYPE_TO_FIELD,
)
from staffing_tool.metrics import REQUIRED_TOTAL, compute_week_metrics
from staffing_tool.models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyStaffing,
)
from staffing_tool.monthly_report import export_monthly_report
from staffing_tool.rag import evaluate_rag
from staffing_tool.report import export_board_pack
from staffing_tool.schedule_import import (  # type: ignore[attr-defined]
    AggregatedWeek,
    aggregate_week_from_records,
    detect_schedule_week_starts,
    parse_schedule_workbook,
)
from staffing_tool.validation import notes_required

from .forms import BaseCoverageFormSet, BaseTotalsFormSet, WeekForm

BASES = ["Bedford", "Lawrence", "Mansfield", "Manchester", "Plymouth"]


def _ops_coverage_total(
    ops: tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]] | None,
) -> int:
    if not ops:
        return 0
    return sum(sum(d.values()) for d in ops)


def _agg_leave_total(agg: AggregatedWeek) -> int:
    return (
        agg.leave_at
        + agg.leave_lt
        + agg.leave_sick
        + agg.leave_loa
        + agg.leave_jury
        + getattr(agg, "leave_brev", 0)
    )


def _build_import_preview_context(
    upload_path: str, week_start_hint: str
) -> dict | None:
    """Context for import preview, or None if no week headers were found in the file."""
    detected = detect_schedule_week_starts(upload_path)
    if not detected:
        return None
    ws_show = week_start_hint if week_start_hint in detected else detected[0]
    records, issues, _ = parse_schedule_workbook(upload_path, week_start=ws_show)
    filled_count = sum(1 for r in records if r.filled)
    ot_count = sum(1 for r in records if r.overtime)
    leave_count = sum(1 for r in records if r.leave_type)
    unknown_units = [i for i in issues if i.issue_type == "unknown_unit"]
    return {
        "week_start": ws_show,
        "detected_weeks": detected,
        "upload_path": upload_path,
        "records_count": len(records),
        "filled_count": filled_count,
        "ot_count": ot_count,
        "leave_count": leave_count,
        "issues": issues,
        "unknown_units": unknown_units,
    }


DB_PATH = getattr(settings, "STAFFING_DB_PATH", None)
OUTPUT_DIR = getattr(settings, "STAFFING_OUTPUT_DIR", None)

# Uploaded schedule workbooks: `schedule_upload_<timestamp>.xlsx`
_SCHEDULE_UPLOAD_PREFIX = "schedule_upload_"


def _schedule_upload_dir() -> str:
    root_dir = os.path.dirname(DB_PATH) if DB_PATH else os.getcwd()
    return os.path.join(root_dir, "uploads")


def _cleanup_old_schedule_uploads(upload_dir: str) -> None:
    """
    Delete files older than STAFFING_UPLOAD_RETENTION_HOURS (default 24).
    Only removes names matching schedule_upload_*.xlsx.
    """
    hours = getattr(settings, "STAFFING_UPLOAD_RETENTION_HOURS", 24)
    try:
        hours_f = float(hours)
    except (TypeError, ValueError):
        hours_f = 24.0
    if hours_f <= 0:
        return
    cutoff = datetime.now(UTC).timestamp() - hours_f * 3600
    if not os.path.isdir(upload_dir):
        return
    for name in os.listdir(upload_dir):
        if not name.startswith(_SCHEDULE_UPLOAD_PREFIX) or not name.endswith(".xlsx"):
            continue
        path = os.path.join(upload_dir, name)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
        except OSError:
            continue


def _resolve_output_dir() -> str:
    """Directory for Excel exports; default next to DB or cwd ``output``."""
    if OUTPUT_DIR:
        return OUTPUT_DIR
    if DB_PATH:
        return os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "output")
    return "output"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_db():
    """Create DB if missing; run migrations (e.g. add leave_brev) on existing DBs."""
    if DB_PATH:
        init_db(DB_PATH)


def _last_sunday():
    today = datetime.now().date()
    days_back = (today.weekday() + 1) % 7
    sun = today - timedelta(days=days_back)
    return sun.strftime("%Y-%m-%d")


# Home overview cards: (card label, KpiThreshold metric_name) — values are rolling averages
HOME_OVERVIEW_METRICS = [
    ("Avg staffing rate", "Staffing Rate"),
    ("Avg OT dependency", "OT Dependency"),
    ("Avg shift exception %", "Leave Exposure"),
    ("Avg system RW coverage", "System RW Coverage %"),
    ("Avg system GR coverage", "System GR Coverage %"),
]


def _home_rolling_averages(metrics_list):
    """Mean of each board KPI across weekly metrics (same thresholds, averaged values)."""
    n = len(metrics_list)
    if not n:
        return {}
    return {
        "Staffing Rate": sum(m.staffing_rate for m in metrics_list) / n,
        "OT Dependency": sum(m.ot_dependency for m in metrics_list) / n,
        "Leave Exposure": sum(m.leave_exposure for m in metrics_list) / n,
        "System RW Coverage %": sum(m.system_rw_pct for m in metrics_list) / n,
        "System GR Coverage %": sum(m.system_gr_pct for m in metrics_list) / n,
    }


def home(request):
    _ensure_db()
    last_sunday = _last_sunday()
    context = {
        "last_sunday": last_sunday,
        "latest_week_start": None,
        "latest_updated_at": None,
        "overview_kpis": [],
        "overview_red_count": 0,
        "overview_yellow_count": 0,
        "recent_weeks": [],
        "overview_weeks_count": 0,
        "overview_range_label": "",
    }
    if not DB_PATH:
        return render(request, "dashboard/home.html", context)

    with session_scope(DB_PATH) as session:
        week_rows = (
            session.query(WeeklyStaffing)
            .order_by(WeeklyStaffing.week_start.desc())
            .limit(4)
            .all()
        )
        if not week_rows:
            return render(request, "dashboard/home.html", context)

        week_starts = [w.week_start for w in week_rows]
        cov_rows = (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start.in_(week_starts))
            .all()
        )
        coverages_by_week = defaultdict(list)
        for c in cov_rows:
            coverages_by_week[c.week_start].append(c)

        bases = list(session.query(BaseConfig).all())
        thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
        th_staffing = thresholds.get("Staffing Rate")

        metrics_list = []
        recent_weeks = []
        for row in week_rows:
            m = compute_week_metrics(row, coverages_by_week[row.week_start], bases)
            metrics_list.append(m)
            rag = evaluate_rag(m.staffing_rate, th_staffing) if th_staffing else "—"
            recent_weeks.append(
                {
                    "week_start": row.week_start,
                    "rate_pct": round(m.staffing_rate * 100, 1),
                    "ot_pct": round(m.ot_dependency * 100, 1),
                    "leave_pct": round(m.leave_exposure * 100, 1),
                    "rw_pct": round(m.system_rw_pct * 100, 1),
                    "gr_pct": round(m.system_gr_pct * 100, 1),
                    "rag": rag,
                }
            )

        latest = week_rows[0]
        m_latest = metrics_list[0]
        avgs = _home_rolling_averages(metrics_list)
        kpis = []
        red_n = yellow_n = 0
        for label, internal in HOME_OVERVIEW_METRICS:
            val = avgs.get(internal)
            if val is None:
                continue
            th = thresholds.get(internal)
            if th:
                rag = evaluate_rag(val, th)
                if rag == "Red":
                    red_n += 1
                elif rag == "Yellow":
                    yellow_n += 1
            else:
                rag = "—"
            kpis.append(
                {
                    "label": label,
                    "value_pct": round(val * 100, 1),
                    "rag": rag,
                }
            )

        n_weeks = len(metrics_list)
        range_label = f"{week_rows[-1].week_start} → {week_rows[0].week_start}"

        context.update(
            {
                "latest_week_start": latest.week_start,
                "latest_updated_at": latest.updated_at,
                "overview_kpis": kpis,
                "overview_red_count": red_n,
                "overview_yellow_count": yellow_n,
                "latest_filled_total": m_latest.filled_total,
                "latest_required_total": m_latest.required_total,
                "latest_vacancies": m_latest.vacancies,
                "recent_weeks": recent_weeks,
                "overview_weeks_count": n_weeks,
                "overview_range_label": range_label,
            }
        )

    return render(request, "dashboard/home.html", context)


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
                records, issues, ops_coverage = parse_schedule_workbook(
                    upload_path, week_start=week_start, unit_overrides=unit_overrides
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


def base_totals(request):
    _ensure_db()
    if request.method == "POST":
        formset = BaseTotalsFormSet(request.POST)
        if formset.is_valid():
            try:
                with session_scope(DB_PATH) as session:
                    for form in formset:
                        if form.cleaned_data.get("base_name"):
                            base = form.cleaned_data["base_name"]
                            rw = form.cleaned_data.get("rw_total") or 0
                            gr = form.cleaned_data.get("gr_total") or 0
                            row = (
                                session.query(BaseConfig)
                                .filter(BaseConfig.base_name == base)
                                .first()
                            )
                            if row:
                                row.rw_total_unit_days = rw
                                row.gr_total_unit_days = gr
                                row.updated_at = _utc_now_iso()
                messages.success(request, "Base totals saved.")
                return redirect("base_totals")
            except Exception as e:
                messages.error(request, str(e))
    else:
        initial = []
        with session_scope(DB_PATH) as session:
            for base in BASES:
                row = (
                    session.query(BaseConfig)
                    .filter(BaseConfig.base_name == base)
                    .first()
                )
                initial.append(
                    {
                        "base_name": base,
                        "rw_total": row.rw_total_unit_days if row else 0,
                        "gr_total": row.gr_total_unit_days if row else 0,
                    }
                )
        formset = BaseTotalsFormSet(initial=initial)
    base_forms = list(zip(BASES, formset))
    return render(
        request,
        "dashboard/base_totals.html",
        {"formset": formset, "bases": BASES, "base_forms": base_forms},
    )


def week_list(request):
    _ensure_db()
    n = 12
    weeks = []
    with session_scope(DB_PATH) as session:
        rows = (
            session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start.desc())
            .limit(n)
            .all()
        )
        week_starts = [r[0] for r in reversed(rows)]
        if not week_starts:
            return render(request, "dashboard/week_list.html", {"weeks": weeks})

        bases = list(session.query(BaseConfig).all())
        thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
        th = thresholds.get("Staffing Rate")

        staff_rows = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start.in_(week_starts))
            .all()
        )
        staff_by_week = {r.week_start: r for r in staff_rows}

        cov_rows = (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start.in_(week_starts))
            .all()
        )
        coverages_by_week = defaultdict(list)
        for c in cov_rows:
            coverages_by_week[c.week_start].append(c)

        for ws in week_starts:
            row = staff_by_week.get(ws)
            if not row:
                continue
            coverages = coverages_by_week.get(ws, [])
            m = compute_week_metrics(row, coverages, bases)
            rag = evaluate_rag(m.staffing_rate, th) if th else "—"
            weeks.append(
                {
                    "week_start": ws,
                    "metrics": m,
                    "rag": rag,
                    "rate_pct": round(m.staffing_rate * 100, 1),
                    "ot_pct": round(m.ot_dependency * 100, 1),
                    "leave_pct": round(m.leave_exposure * 100, 1),
                }
            )
    return render(request, "dashboard/week_list.html", {"weeks": weeks})


def _get_week_form_initial(week_start, session):
    row = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    if not row:
        return None
    # Reload from DB so the form matches the latest save (e.g. right after schedule import).
    session.refresh(row)
    coverages = (
        session.query(WeeklyBaseCoverage)
        .filter(WeeklyBaseCoverage.week_start == week_start)
        .all()
    )
    cov_by_base = {c.base_name: c for c in coverages}
    initial = {
        "week_start": row.week_start,
        "filled_day": row.filled_day,
        "filled_night": row.filled_night,
        # Only explicit day/night columns — do not fold legacy ot_rn/ot_medic/ot_emt into Day.
        "ot_rn_day": getattr(row, "ot_rn_day", 0) or 0,
        "ot_rn_night": getattr(row, "ot_rn_night", 0) or 0,
        "ot_medic_day": getattr(row, "ot_medic_day", 0) or 0,
        "ot_medic_night": getattr(row, "ot_medic_night", 0) or 0,
        "ot_emt_day": getattr(row, "ot_emt_day", 0) or 0,
        "ot_emt_night": getattr(row, "ot_emt_night", 0) or 0,
        "leave_at": row.leave_at,
        "leave_lt": row.leave_lt,
        "leave_sick": row.leave_sick,
        "leave_loa": row.leave_loa,
        "leave_jury": getattr(row, "leave_jury", 0),
        "leave_brev": getattr(row, "leave_brev", 0),
        "medic_unpartnered": getattr(row, "medic_unpartnered", 0) or 0,
        "rn_unpartnered_staff": getattr(row, "rn_unpartnered_staff", 0) or 0,
        "notes": row.notes or "",
    }
    coverage_initial = []
    for base in BASES:
        c = cov_by_base.get(base)
        if c:
            rw_d = getattr(c, "rw_staffed_day", 0) or 0
            rw_n = getattr(c, "rw_staffed_night", 0) or 0
            gr_d = getattr(c, "gr_staffed_day", 0) or 0
            gr_n = getattr(c, "gr_staffed_night", 0) or 0
            if rw_d + rw_n == 0 and (c.rw_staffed_unit_days or 0) > 0:
                rw_d = c.rw_staffed_unit_days
            if gr_d + gr_n == 0 and (c.gr_staffed_unit_days or 0) > 0:
                gr_d = c.gr_staffed_unit_days
        else:
            rw_d = rw_n = gr_d = gr_n = 0
        coverage_initial.append(
            {
                "base_name": base,
                "rw_staffed_day": rw_d,
                "rw_staffed_night": rw_n,
                "gr_staffed_day": gr_d,
                "gr_staffed_night": gr_n,
            }
        )
    return initial, coverage_initial


def week_edit(request, week_start):
    _ensure_db()
    if request.method == "POST":
        form = WeekForm(request.POST, prefix="week")
        formset = BaseCoverageFormSet(request.POST, prefix="cov")
        if form.is_valid() and formset.is_valid():
            _save_week_and_coverage(request, form.cleaned_data, formset, week_start)
            return redirect("week_list")
        leave_detail_map, _ = _parse_exception_grid_post(request.POST)
        leave_grid_rows = _build_leave_grid_rows(leave_detail_map)
    else:
        with session_scope(DB_PATH) as session:
            data = _get_week_form_initial(week_start, session)
            if not data:
                messages.error(
                    request, f"No data for week {week_start}. Use Add week instead."
                )
                return redirect("week_list")
            initial, coverage_initial = data
            leave_details = (
                session.query(WeeklyLeaveDetail)
                .filter(WeeklyLeaveDetail.week_start == week_start)
                .all()
            )
            leave_breakdown = {(r.role, r.leave_type): r.count for r in leave_details}
        form = WeekForm(initial=initial, prefix="week")
        formset = BaseCoverageFormSet(initial=coverage_initial, prefix="cov")
        leave_grid_rows = _build_leave_grid_rows(leave_breakdown)
    coverage_forms = list(zip(BASES, formset))
    return render(
        request,
        "dashboard/week_edit.html",
        {
            "form": form,
            "formset": formset,
            "week_start": week_start,
            "bases": BASES,
            "coverage_forms": coverage_forms,
            "is_add": False,
            "leave_types_order": EXCEPTION_GRID_COLS,
            "leave_grid_rows": leave_grid_rows,
        },
    )


def week_add(request):
    _ensure_db()
    last_sun = _last_sunday()
    if request.method == "POST":
        form = WeekForm(request.POST, prefix="week")
        formset = BaseCoverageFormSet(request.POST, prefix="cov")
        if form.is_valid() and formset.is_valid():
            week_start = form.cleaned_data.get("week_start") or last_sun
            _save_week_and_coverage(request, form.cleaned_data, formset, week_start)
            return redirect("week_list")
        leave_detail_map, _ = _parse_exception_grid_post(request.POST)
        leave_grid_rows = _build_leave_grid_rows(leave_detail_map)
    else:
        form = WeekForm(initial={"week_start": last_sun}, prefix="week")
        formset = BaseCoverageFormSet(
            initial=[
                {
                    "base_name": b,
                    "rw_staffed_day": 0,
                    "rw_staffed_night": 0,
                    "gr_staffed_day": 0,
                    "gr_staffed_night": 0,
                }
                for b in BASES
            ],
            prefix="cov",
        )
        leave_grid_rows = _build_leave_grid_rows({})
    coverage_forms = list(zip(BASES, formset))
    return render(
        request,
        "dashboard/week_edit.html",
        {
            "form": form,
            "formset": formset,
            "week_start": None,
            "bases": BASES,
            "coverage_forms": coverage_forms,
            "is_add": True,
            "leave_types_order": EXCEPTION_GRID_COLS,
            "leave_grid_rows": leave_grid_rows,
        },
    )


def _int(val):
    if val is None or val == "":
        return 0
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _exception_cell_from_breakdown(breakdown, role, col):
    keys = EXCEPTION_COL_BREAKDOWN_KEYS[col]
    return sum(breakdown.get((role, k), 0) for k in keys)


def _build_leave_grid_rows(breakdown):
    """Rows for template: each has role + cells with name/value for number inputs."""
    rows = []
    for role in EXCEPTION_GRID_ROLES:
        cells = [
            {
                "name": f"exc_{role}_{col}",
                "value": _exception_cell_from_breakdown(breakdown, role, col),
            }
            for col in EXCEPTION_GRID_COLS
        ]
        rows.append({"role": role, "cells": cells})
    return rows


def _parse_exception_grid_post(post):
    """
    Read exc_<Role>_<Col> inputs. Returns (detail_map for WeeklyLeaveDetail, col_totals).
    """
    detail = {}
    col_totals = {c: 0 for c in EXCEPTION_GRID_COLS}
    for role in EXCEPTION_GRID_ROLES:
        for col in EXCEPTION_GRID_COLS:
            v = max(0, _int(post.get(f"exc_{role}_{col}")))
            col_totals[col] += v
            if v > 0:
                detail[(role, EXCEPTION_COL_DB_TYPE[col])] = v
    return detail, col_totals


def _merge_leave_totals_from_grid(data, col_totals):
    """Copy data and set leave_* fields from grid column sums (matches WeeklyStaffing columns)."""
    out = dict(data)
    for col in EXCEPTION_GRID_COLS:
        field = LEAVE_TYPE_TO_FIELD.get(col)
        if field:
            out[field] = col_totals.get(col, 0)
    return out


def _save_week_and_coverage(request, data, formset, week_start):
    week_start = (data.get("week_start") or week_start or "").strip()
    if not week_start:
        messages.error(request, "Week start (Sunday) is required.")
        return
    try:
        d = datetime.strptime(week_start, "%Y-%m-%d")
        if d.weekday() != 6:
            messages.error(request, "Week start must be a Sunday.")
            return
    except ValueError:
        messages.error(request, "Week start must be YYYY-MM-DD.")
        return

    leave_detail_map, leave_col_totals = _parse_exception_grid_post(request.POST)
    data = _merge_leave_totals_from_grid(data, leave_col_totals)

    filled_day = _int(data.get("filled_day"))
    filled_night = _int(data.get("filled_night"))
    required_total = REQUIRED_TOTAL
    filled_total = filled_day + filled_night
    ot_rn_day = _int(data.get("ot_rn_day"))
    ot_rn_night = _int(data.get("ot_rn_night"))
    ot_medic_day = _int(data.get("ot_medic_day"))
    ot_medic_night = _int(data.get("ot_medic_night"))
    ot_emt_day = _int(data.get("ot_emt_day"))
    ot_emt_night = _int(data.get("ot_emt_night"))
    ot_rn = ot_rn_day + ot_rn_night
    ot_medic = ot_medic_day + ot_medic_night
    ot_emt = ot_emt_day + ot_emt_night
    ot_shifts = ot_rn + ot_medic + ot_emt
    staffing_rate = filled_total / required_total if required_total else 0
    # UI validation uses the same definition as metrics: OT / filled_total.
    ot_dependency = ot_shifts / filled_total if filled_total else 0
    notes = (data.get("notes") or "").strip()

    now = _utc_now_iso()
    with session_scope(DB_PATH) as session:
        base_by_name = {b.base_name: b for b in session.query(BaseConfig).all()}

        base_staffed_gt = False
        for form in formset:
            if not form.cleaned_data.get("base_name"):
                continue
            base = form.cleaned_data["base_name"]
            rw_s = _int(form.cleaned_data.get("rw_staffed_day")) + _int(
                form.cleaned_data.get("rw_staffed_night")
            )
            gr_s = _int(form.cleaned_data.get("gr_staffed_day")) + _int(
                form.cleaned_data.get("gr_staffed_night")
            )
            cfg = base_by_name.get(base)
            if not cfg:
                continue
            rw_cap = int(cast(int, cfg.rw_total_unit_days) or 0)
            gr_cap = int(cast(int, cfg.gr_total_unit_days) or 0)
            if rw_cap == 0 and rw_s > 0:
                messages.error(
                    request,
                    f"Base {base} has RW total = 0. Set base totals first.",
                )
                return
            if gr_cap == 0 and gr_s > 0:
                messages.error(
                    request,
                    f"Base {base} has GR total = 0. Set base totals first.",
                )
                return
            if (rw_cap and rw_s > rw_cap) or (gr_cap and gr_s > gr_cap):
                base_staffed_gt = True

        if (
            notes_required(
                staffing_rate,
                ot_dependency,
                filled_total,
                required_total=required_total,
                base_staffed_gt_total=base_staffed_gt,
            )
            and not notes
        ):
            messages.error(
                request,
                "Notes are required when staffing rate < 90%, OT dependency > 12%, "
                "any base staffed > total, or filled > required+10.",
            )
            return

        row = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start == week_start)
            .first()
        )
        if row:
            row.filled_day = filled_day
            row.filled_night = filled_night
            row.ot_shifts = ot_shifts
            row.ot_rn = ot_rn
            row.ot_medic = ot_medic
            row.ot_emt = ot_emt
            row.ot_rn_day = ot_rn_day
            row.ot_rn_night = ot_rn_night
            row.ot_medic_day = ot_medic_day
            row.ot_medic_night = ot_medic_night
            row.ot_emt_day = ot_emt_day
            row.ot_emt_night = ot_emt_night
            row.leave_at = _int(data.get("leave_at"))
            row.leave_lt = _int(data.get("leave_lt"))
            row.leave_sick = _int(data.get("leave_sick"))
            row.leave_loa = _int(data.get("leave_loa"))
            row.leave_jury = _int(data.get("leave_jury"))
            row.leave_brev = _int(data.get("leave_brev"))
            row.medic_unpartnered = _int(data.get("medic_unpartnered"))
            row.rn_unpartnered_staff = _int(data.get("rn_unpartnered_staff"))
            row.overnights_below = 0
            row.pilot_vacancies = 0
            row.notes = notes or None
            row.updated_at = now
        else:
            row = WeeklyStaffing(
                week_start=week_start,
                day_target=8,
                night_min=4,
                filled_day=filled_day,
                filled_night=filled_night,
                ot_shifts=ot_shifts,
                ot_rn=ot_rn,
                ot_medic=ot_medic,
                ot_emt=ot_emt,
                ot_rn_day=ot_rn_day,
                ot_rn_night=ot_rn_night,
                ot_medic_day=ot_medic_day,
                ot_medic_night=ot_medic_night,
                ot_emt_day=ot_emt_day,
                ot_emt_night=ot_emt_night,
                leave_at=_int(data.get("leave_at")),
                leave_lt=_int(data.get("leave_lt")),
                leave_sick=_int(data.get("leave_sick")),
                leave_loa=_int(data.get("leave_loa")),
                leave_jury=_int(data.get("leave_jury")),
                leave_brev=_int(data.get("leave_brev")),
                medic_unpartnered=_int(data.get("medic_unpartnered")),
                rn_unpartnered_staff=_int(data.get("rn_unpartnered_staff")),
                overnights_below=0,
                pilot_vacancies=0,
                notes=notes or None,
                entered_by=request.user.username
                if request.user.is_authenticated
                else "web",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        session.flush()
        for form in formset:
            if form.cleaned_data.get("base_name"):
                base = form.cleaned_data["base_name"]
                rw_d = _int(form.cleaned_data.get("rw_staffed_day"))
                rw_n = _int(form.cleaned_data.get("rw_staffed_night"))
                gr_d = _int(form.cleaned_data.get("gr_staffed_day"))
                gr_n = _int(form.cleaned_data.get("gr_staffed_night"))
                rw_s = rw_d + rw_n
                gr_s = gr_d + gr_n
                rec = (
                    session.query(WeeklyBaseCoverage)
                    .filter(
                        WeeklyBaseCoverage.week_start == week_start,
                        WeeklyBaseCoverage.base_name == base,
                    )
                    .first()
                )
                if rec:
                    rec.rw_staffed_day = rw_d
                    rec.rw_staffed_night = rw_n
                    rec.gr_staffed_day = gr_d
                    rec.gr_staffed_night = gr_n
                    rec.rw_staffed_unit_days = rw_s
                    rec.gr_staffed_unit_days = gr_s
                else:
                    session.add(
                        WeeklyBaseCoverage(
                            week_start=week_start,
                            base_name=base,
                            rw_staffed_unit_days=rw_s,
                            gr_staffed_unit_days=gr_s,
                            rw_staffed_day=rw_d,
                            rw_staffed_night=rw_n,
                            gr_staffed_day=gr_d,
                            gr_staffed_night=gr_n,
                        )
                    )
        session.query(WeeklyLeaveDetail).filter(
            WeeklyLeaveDetail.week_start == week_start
        ).delete()
        for (role, leave_type), count in leave_detail_map.items():
            session.add(
                WeeklyLeaveDetail(
                    week_start=week_start,
                    role=role,
                    leave_type=leave_type,
                    count=count,
                )
            )
    messages.success(request, f"Week {week_start} saved.")


def week_delete(request, week_start):
    """Confirm and delete a week (and its base coverage + position grid)."""
    _ensure_db()
    if request.method == "POST":
        with session_scope(DB_PATH) as session:
            row = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == week_start)
                .first()
            )
            if row:
                session.delete(row)
                messages.success(request, f"Week {week_start} deleted.")
            else:
                messages.error(request, f"No data for week {week_start}.")
        return redirect("week_list")
    return render(
        request, "dashboard/week_confirm_delete.html", {"week_start": week_start}
    )


def export_excel(request, week_start):
    _ensure_db()
    try:
        path = export_board_pack(
            DB_PATH, week_start, trend_weeks=12, output_dir=_resolve_output_dir()
        )
        if not path or not os.path.isfile(path):
            raise Http404("Export file not found")
        filename = os.path.basename(path)
        return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)
    except Exception as e:
        raise Http404(str(e))


def _default_previous_calendar_month():
    """First and last day of the previous calendar month (ISO dates)."""
    today = date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev.isoformat(), last_prev.isoformat()


def monthly_report(request):
    """Pick a date range and download a BMF-styled monthly Excel aggregate."""
    _ensure_db()
    default_start, default_end = _default_previous_calendar_month()
    if not DB_PATH:
        messages.error(request, "Database is not configured (STAFFING_DB_PATH).")
        return redirect("home")

    if request.method == "POST":
        start = (request.POST.get("date_start") or "").strip()
        end = (request.POST.get("date_end") or "").strip()
        try:
            path = export_monthly_report(
                DB_PATH, start, end, output_dir=_resolve_output_dir()
            )
            if not path or not os.path.isfile(path):
                raise Http404("Export file not found")
            return FileResponse(
                open(path, "rb"), as_attachment=True, filename=os.path.basename(path)
            )
        except ValueError as exc:
            messages.error(request, str(exc))
        except Http404:
            raise
        except Exception as exc:
            messages.error(request, f"Export failed: {exc}")
        return render(
            request,
            "dashboard/monthly_report.html",
            {
                "date_start": start or default_start,
                "date_end": end or default_end,
            },
        )

    return render(
        request,
        "dashboard/monthly_report.html",
        {
            "date_start": default_start,
            "date_end": default_end,
        },
    )
