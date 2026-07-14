"""
Monthly staffing board report — polished HTML export.

Board-level summary for a date range (usually one calendar month): core
staffing KPIs averaged over the period with change vs the prior period,
weekly trend and exception-mix charts, OT by role, base coverage, and a
week-by-week detail table. Same visual family as the weekly and quarterly
HTML reports (see staffing_tool/report_html.py); the Excel export in
monthly_report.py remains the working/analyst format.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

from staffing_tool import report_html as rh
from staffing_tool.db import session_scope
from staffing_tool.leave_grid import EXCEPTION_GRID_COLS
from staffing_tool.metrics import (
    BASE_DISPLAY_ORDER,
    PeriodRollups,
    RoleFill,
    compute_period_rollups,
    compute_role_fill,
    compute_week_metrics,
)
from staffing_tool.models import BaseConfig, WeeklyBaseCoverage, WeeklyStaffing

EM = rh.EM
BASE_ORDER = BASE_DISPLAY_ORDER


def _pct(v: float) -> str:
    return f"{100 * v:.1f}%"


def _short_label(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return d.strftime("%b ") + str(d.day)


@dataclass
class MonthlyBoardData:
    date_start: str
    date_end: str
    weeks_count: int
    rollups: PeriodRollups
    prior_rollups: PeriodRollups | None
    prior_label: str
    weekly_trend: list[tuple[str, float, float, float]]
    weekly_detail: list[tuple[str, str, str, str, str]]
    leave_breakdown: list[tuple[str, int]]
    ot_by_role: list[tuple[str, int]]
    base_coverage: list[tuple[str, str, str, str, str]]
    role_fill: list[RoleFill]


def _period_rollups(session, start_s: str, end_s: str) -> PeriodRollups | None:
    """Rollups for weeks whose Sunday week_start falls inside the window."""
    weeks = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start >= start_s)
        .filter(WeeklyStaffing.week_start <= end_s)
        .order_by(WeeklyStaffing.week_start)
        .all()
    )
    if not weeks:
        return None
    base_configs = session.query(BaseConfig).all()
    metrics = []
    for row in weeks:
        coverages = (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start == row.week_start)
            .all()
        )
        metrics.append(compute_week_metrics(row, coverages, base_configs))
    return compute_period_rollups(metrics)


def load_monthly_board_data(
    db_path: str, date_start: str, date_end: str
) -> MonthlyBoardData:
    try:
        ds = datetime.strptime(date_start, "%Y-%m-%d").date()
        de = datetime.strptime(date_end, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError("date_start and date_end must be YYYY-MM-DD.") from exc
    if ds > de:
        raise ValueError("date_start must be on or before date_end.")

    with session_scope(db_path) as session:
        week_rows = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start >= date_start)
            .filter(WeeklyStaffing.week_start <= date_end)
            .order_by(WeeklyStaffing.week_start)
            .all()
        )
        if not week_rows:
            raise ValueError(f"No staffing weeks between {date_start} and {date_end}.")
        base_configs = session.query(BaseConfig).all()
        cfg_by_name = {b.base_name: b for b in base_configs}

        weekly_trend: list[tuple[str, float, float, float]] = []
        weekly_detail: list[tuple[str, str, str, str, str]] = []
        leave_totals = {code: 0 for code in EXCEPTION_GRID_COLS}
        ot_rn = ot_medic = ot_emt = 0
        base_rw: dict[str, int] = {b: 0 for b in BASE_ORDER}
        base_gr: dict[str, int] = {b: 0 for b in BASE_ORDER}
        metrics_list = []

        for row in week_rows:
            coverages = (
                session.query(WeeklyBaseCoverage)
                .filter(WeeklyBaseCoverage.week_start == row.week_start)
                .all()
            )
            wm = compute_week_metrics(row, coverages, base_configs)
            metrics_list.append(wm)
            label = _short_label(str(row.week_start))
            weekly_trend.append(
                (
                    label,
                    wm.staffing_rate * 100,
                    wm.ot_dependency * 100,
                    wm.leave_exposure * 100,
                )
            )
            weekly_detail.append(
                (
                    label,
                    _pct(wm.staffing_rate),
                    _pct(wm.ot_dependency),
                    _pct(wm.leave_exposure),
                    str(wm.vacancies),
                )
            )
            ws_row = cast(Any, row)
            leave_totals["AT"] += int(ws_row.leave_at or 0)
            leave_totals["LT"] += int(ws_row.leave_lt or 0)
            leave_totals["SICK"] += int(ws_row.leave_sick or 0)
            leave_totals["LOA"] += int(ws_row.leave_loa or 0) + int(
                ws_row.leave_pfml or 0
            )
            leave_totals["JURY"] += int(ws_row.leave_jury or 0)
            leave_totals["BREV"] += int(ws_row.leave_brev or 0)
            ot_rn += int(ws_row.ot_rn or 0)
            ot_medic += int(ws_row.ot_medic or 0)
            ot_emt += int(ws_row.ot_emt or 0)
            bm = wm.base_metrics or {}
            for base in BASE_ORDER:
                m = bm.get(base, {})
                base_rw[base] += int(m.get("rw_staffed", 0))
                base_gr[base] += int(m.get("gr_staffed", 0))

        rollups = compute_period_rollups(metrics_list)
        if rollups is None:
            raise ValueError("Could not compute period rollups.")

        # Prior window of the same length, ending the day before the start.
        window_days = (de - ds).days
        prior_end = ds - timedelta(days=1)
        prior_start = prior_end - timedelta(days=window_days)
        prior_rollups = _period_rollups(
            session, prior_start.isoformat(), prior_end.isoformat()
        )
        prior_label = f"{prior_start:%b %d} – {prior_end:%b %d, %Y}"

        n = rollups.n_weeks
        base_coverage: list[tuple[str, str, str, str, str]] = []
        for base in BASE_ORDER:
            cfg = cfg_by_name.get(base)
            rw_cap = int(cfg.rw_total_unit_days) * n if cfg else 0
            gr_cap = int(cfg.gr_total_unit_days) * n if cfg else 0
            rw_n = base_rw.get(base, 0)
            gr_n = base_gr.get(base, 0)
            base_coverage.append(
                (
                    base,
                    str(rw_n) if rw_n else EM,
                    _pct(rw_n / rw_cap)
                    if rw_cap and rw_n
                    else (EM if not rw_cap else "0.0%"),
                    str(gr_n) if gr_n else EM,
                    _pct(gr_n / gr_cap)
                    if gr_cap and gr_n
                    else (EM if not gr_cap else "0.0%"),
                )
            )

        return MonthlyBoardData(
            date_start=date_start,
            date_end=date_end,
            weeks_count=n,
            rollups=rollups,
            prior_rollups=prior_rollups,
            prior_label=prior_label,
            weekly_trend=weekly_trend,
            weekly_detail=weekly_detail,
            leave_breakdown=[
                (code, leave_totals[code]) for code in EXCEPTION_GRID_COLS
            ],
            ot_by_role=[("RN", ot_rn), ("Paramedic", ot_medic), ("EMT", ot_emt)],
            base_coverage=base_coverage,
            role_fill=compute_role_fill(
                session, [str(row.week_start) for row in week_rows]
            ),
        )


def _board_kpis(data: MonthlyBoardData) -> list[tuple]:
    """KPI cells with change vs the prior period when it has data."""
    r, p = data.rollups, data.prior_rollups
    spec = [
        ("Staffing Rate", r.avg_staffing_rate, "avg_staffing_rate", True),
        ("Day Fill", r.avg_day_staffing_rate, "avg_day_staffing_rate", True),
        ("Night Fill", r.avg_night_staffing_rate, "avg_night_staffing_rate", True),
        ("OT Dependency", r.avg_ot_dependency, "avg_ot_dependency", False),
        ("Shift Exception %", r.avg_leave_exposure, "avg_leave_exposure", False),
        ("System RW %", r.avg_system_rw_pct, "avg_system_rw_pct", True),
        ("System GR %", r.avg_system_gr_pct, "avg_system_gr_pct", True),
    ]
    kpis: list[tuple] = []
    for label, value, attr, higher_better in spec:
        if p is not None:
            kpis.append(
                (
                    label,
                    _pct(value),
                    rh.delta_html(
                        value, getattr(p, attr), higher_is_better=higher_better
                    ),
                )
            )
        else:
            kpis.append((label, _pct(value)))
    return kpis


def build_monthly_board_html(data: MonthlyBoardData, output_path: str) -> str:
    # Chart builders are shared with the quarterly report; they only read
    # .weekly_trend / .leave_breakdown, so a namespace stand-in works.
    from staffing_tool.quarterly_pdf_report import (
        _build_exception_bar_fig,
        _build_trend_fig,
    )

    ctx = SimpleNamespace(
        weekly_trend=data.weekly_trend, leave_breakdown=data.leave_breakdown
    )
    trend_b64 = rh.fig_to_png_base64(_build_trend_fig(ctx)) if data.weekly_trend else ""
    exc_b64 = rh.fig_to_png_base64(_build_exception_bar_fig(ctx))

    counts = sorted(data.leave_breakdown, key=lambda r: r[1], reverse=True)
    top2 = {code for code, count in counts[:2] if count > 0}

    body = rh.section_bar("KEY PERFORMANCE INDICATORS — PERIOD AVERAGES")
    kpi_note = (
        rh.note(
            f"Change shown vs prior period ({data.prior_label}), percentage points."
        )
        if data.prior_rollups
        else rh.note("No prior-period data available for comparison.")
    )
    body += rh.body_cell(rh.kpi_strip(_board_kpis(data)) + kpi_note)

    if trend_b64:
        body += rh.section_bar("WEEKLY TREND THIS PERIOD")
        body += rh.body_cell(rh.chart_img(trend_b64, "Weekly staffing trend"))

    grid_label = " &middot; ".join(EXCEPTION_GRID_COLS)
    top2_note = ", ".join(f"{code} ({count})" for code, count in counts if code in top2)
    body += rh.section_bar(f"EXCEPTION BREAKDOWN ({grid_label})")
    body += rh.body_cell(
        rh.chart_img(exc_b64, "Exception breakdown")
        + '<div style="height:12px;"></div>'
        + rh.exception_mix_table(data.leave_breakdown, top2)
        + rh.note(f"Top drivers (red in chart): {top2_note or 'n/a'}.")
    )

    if any(rf.worked for rf in data.role_fill):
        body += rh.section_bar("FILL RATE BY ROLE")
        body += rh.body_cell(
            rh.data_table(
                ["Role", "Worked", "Capacity", "Fill Rate"],
                [
                    [rf.label, str(rf.worked), str(rf.capacity), _pct(rf.rate)]
                    for rf in data.role_fill
                ],
                right_cols={1, 2, 3},
            )
            + rh.note(
                "Worked = staffed + OT person-shifts from the imported "
                "schedules; capacity is the weekly plan per role × weeks."
            )
        )

    ot_total = sum(v for _, v in data.ot_by_role)
    ot_rows = [[label, str(v)] for label, v in data.ot_by_role]
    ot_rows.append(["Total", str(ot_total)])
    body += rh.section_bar("OVERTIME BY ROLE")
    body += rh.body_cell(
        rh.data_table(["Role", "OT Shifts"], ot_rows, right_cols={1}, total_row=True)
    )

    body += rh.section_bar("COVERAGE BY BASE")
    body += rh.body_cell(
        rh.data_table(
            ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"],
            [list(r) for r in data.base_coverage],
            right_cols={1, 2, 3, 4},
        )
    )

    body += rh.section_bar("WEEK-BY-WEEK DETAIL")
    body += rh.body_cell(
        rh.data_table(
            ["Week", "Staffing Rate", "OT Dependency", "Exception %", "Vacancies"],
            [list(r) for r in data.weekly_detail],
            right_cols={1, 2, 3, 4},
        )
    )

    html = rh.report_shell(
        title="MONTHLY STAFFING REPORT",
        subtitle=f"Board summary &nbsp;|&nbsp; {data.date_start} to {data.date_end}",
        meta=(
            f"Weeks included: {data.weeks_count} &middot; "
            f"Prepared {date.today():%B %d, %Y} &middot; CONFIDENTIAL"
        ),
        body=body,
        doc_title=f"Monthly Staffing Report — {data.date_start} to {data.date_end}",
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def export_monthly_report_html(
    db_path: str,
    date_start: str,
    date_end: str,
    output_dir: str,
) -> str:
    data = load_monthly_board_data(db_path, date_start, date_end)
    os.makedirs(output_dir, exist_ok=True)
    safe_start = date_start.replace("-", "")
    safe_end = date_end.replace("-", "")
    out_path = os.path.join(
        output_dir, f"Monthly_staffing_{safe_start}_to_{safe_end}.html"
    )
    return build_monthly_board_html(data, out_path)
