"""Week grid: every vehicle, every day, every seat, editable in one place."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from staffing_tool.db import session_scope
from staffing_tool.metrics import compute_week_metrics
from staffing_tool.models import BaseConfig, WeeklyBaseCoverage, WeeklyStaffing
from staffing_tool.shortfall import analyze_week, reason_table_rows
from staffing_tool.week_grid import (
    MANUAL_ENTRY,
    NOTE_MAX,
    SEAT_LABELS,
    SEATS_BY_SERVICE,
    apply_grid_post,
    has_grid,
    load_week_grid,
    note_field,
    outage_field,
    seat_field,
)

from .helpers import DB_PATH, _ensure_db, backup_staffing_db_before_write


def _summary(session, week_start: str) -> dict[str, object] | None:
    week = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    if week is None:
        return None
    coverages = (
        session.query(WeeklyBaseCoverage)
        .filter(WeeklyBaseCoverage.week_start == week_start)
        .all()
    )
    m = compute_week_metrics(week, coverages, session.query(BaseConfig).all())
    reasons = reason_table_rows(analyze_week(session, week_start, tracked_only=True))
    return {
        "staffing": f"{m.staffing_rate * 100:.1f}%",
        "filled": f"{m.filled_total} / {m.required_total}",
        "rw": f"{m.system_rw_pct * 100:.1f}%",
        "gr": f"{m.system_gr_pct * 100:.1f}%",
        "reasons": reasons,
    }


def week_grid(request, week_start):
    """Show and save the week as a vehicle-by-day grid with out-of-service marks."""
    _ensure_db()
    if request.method == "POST":
        backup_path = backup_staffing_db_before_write()
        with session_scope(DB_PATH) as session:
            if not has_grid(session, week_start):
                messages.error(request, f"No schedule detail for week {week_start}.")
                return redirect("week_list")
            result = apply_grid_post(session, week_start, request.POST)
        if result.changed:
            parts = []
            if result.cells_changed:
                parts.append(f"{result.cells_changed} seat(s) changed")
            if result.outages_added:
                parts.append(f"{result.outages_added} vehicle(s) marked out of service")
            if result.outages_removed:
                parts.append(f"{result.outages_removed} out-of-service mark(s) cleared")
            if result.outage_notes_changed:
                parts.append(f"{result.outage_notes_changed} note(s) updated")
            msg = "Saved: " + ", ".join(parts) + ". Coverage and reasons recalculated."
            if backup_path is None:
                msg += " The automatic safety backup did not run."
            messages.success(request, msg)
        else:
            messages.info(request, "No changes to save.")
        return redirect("week_grid", week_start=week_start)

    with session_scope(DB_PATH) as session:
        if not has_grid(session, week_start):
            messages.error(
                request,
                f"Week {week_start} has no schedule detail to show. "
                "Import its schedule workbook first.",
            )
            return redirect("week_list")
        grid = load_week_grid(session, week_start)
        summary = _summary(session, week_start)

    # One editor per vehicle-day. Short ones (not out of service) go in the
    # "fix these" list; the rest sit behind the week-at-a-glance squares.
    editors: dict[tuple[str, str], dict[str, object]] = {}
    for r in grid.rows:
        iso = r.day.isoformat()
        state = "oos" if r.out_of_service else ("ok" if r.staffed else "short")
        editors[(iso, r.unit)] = {
            "id": f"vd-{iso}-{r.unit}",
            "unit": r.unit,
            "base": r.base,
            "service": r.service,
            "day_night": "night" if r.day_night == "N" else "day",
            "day_label": r.day.strftime("%a %b %d"),
            "extra": r.extra,
            "state": state,
            "missing": ", ".join(r.missing),
            "oos_field": outage_field(r.day, r.unit),
            "out_of_service": r.out_of_service,
            "note_field": note_field(r.day, r.unit),
            "note": r.note,
            "seats": [
                {
                    "label": SEAT_LABELS[seat],
                    "field": seat_field(r.day, r.unit, seat),
                    "value": r.seats[seat],
                    "manual": seat in r.manual,
                }
                for seat in SEATS_BY_SERVICE[r.service]
            ],
        }
    attention = [e for e in editors.values() if e["state"] == "short"]
    others = [e for e in editors.values() if e["state"] != "short"]

    matrix_rows = []
    seen_units: list[str] = []
    for r in grid.rows:
        if r.unit not in seen_units:
            seen_units.append(r.unit)
    for unit in seen_units:
        first = next(r for r in grid.rows if r.unit == unit)
        matrix_rows.append(
            {
                "unit": unit,
                "base": first.base,
                "service": first.service,
                "day_night": "N" if first.day_night == "N" else "D",
                "extra": first.extra,
                "cells": [editors[(d.isoformat(), unit)] for d in grid.days],
            }
        )
    manual_count = sum(len(r.manual) for r in grid.rows)
    oos_count = sum(1 for r in grid.rows if r.out_of_service)
    return render(
        request,
        "dashboard/week_grid.html",
        {
            "week_start": week_start,
            "attention": attention,
            "others": others,
            "matrix_rows": matrix_rows,
            "matrix_days": [
                {"iso": d.isoformat(), "short": d.strftime("%a"), "num": d.day}
                for d in grid.days
            ],
            "manual_count": manual_count,
            "oos_count": oos_count,
            "manual_entry": MANUAL_ENTRY,
            "summary": summary,
            "note_max": NOTE_MAX,
            "imported": request.GET.get("imported") == "1",
        },
    )
