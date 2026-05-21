"""Week list, edit, base totals, Excel export."""

import os
from collections import defaultdict
from datetime import datetime
from typing import cast

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
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
from staffing_tool.rag import evaluate_rag
from staffing_tool.report import export_board_pack
from staffing_tool.validation import notes_required, notes_required_message

from ..forms import BaseCoverageFormSet, BaseTotalsFormSet, WeekForm
from .helpers import (
    BASES,
    DB_PATH,
    _ensure_db,
    _last_sunday,
    _resolve_output_dir,
    _utc_now_iso,
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
        "unpartnered_note_medic": getattr(row, "unpartnered_note_medic", None)
        or "",
        "unpartnered_note_rn": getattr(row, "unpartnered_note_rn", None) or "",
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


def _short_note(val, max_len: int = 200) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    return s[:max_len]


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
        thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
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
                thresholds=thresholds,
            )
            and not notes
        ):
            messages.error(request, notes_required_message(thresholds))
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
            row.unpartnered_note_medic = _short_note(
                data.get("unpartnered_note_medic")
            )
            row.unpartnered_note_rn = _short_note(data.get("unpartnered_note_rn"))
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
                unpartnered_note_medic=_short_note(
                    data.get("unpartnered_note_medic")
                ),
                unpartnered_note_rn=_short_note(data.get("unpartnered_note_rn")),
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
        raw_name = request.session.get("schedule_upload_original_name")
        source_filename = (
            raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "(web export)"
        )
        with session_scope(DB_PATH) as session:
            n_base = (
                session.query(WeeklyBaseCoverage)
                .filter(WeeklyBaseCoverage.week_start == week_start)
                .count()
            )
            n_exc = (
                session.query(WeeklyLeaveDetail)
                .filter(WeeklyLeaveDetail.week_start == week_start)
                .count()
            )
        metadata = {
            "source_filename": source_filename,
            "source_rows": 1 + n_base + n_exc,
        }
        path = export_board_pack(
            DB_PATH,
            week_start,
            trend_weeks=12,
            output_dir=_resolve_output_dir(),
            metadata=metadata,
        )
        if not path or not os.path.isfile(path):
            raise Http404("Export file not found")
        filename = os.path.basename(path)
        return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)
    except Exception as e:
        raise Http404(str(e))
