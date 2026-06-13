"""
Weekly staffing PDF + HTML report builder (data from staffing.db).

Visual style: staffing_tool/report_style.py + docs/BMF_Visual_Style_Spec.md
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from staffing_tool import report_style as style
from staffing_tool.db import session_scope
from staffing_tool.leave_grid import (
    EXCEPTION_COL_BREAKDOWN_KEYS,
    EXCEPTION_GRID_COLS,
    EXCEPTION_GRID_ROLES,
)
from staffing_tool.manager_roster import manager_last_names_upper_from_session
from staffing_tool.metrics import (
    REQUIRED_DAY,
    REQUIRED_NIGHT,
    REQUIRED_TOTAL,
    TOTAL_PERSON_SHIFTS,
    compute_week_metrics,
)
from staffing_tool.models import (
    BaseConfig,
    WeeklyBaseCoverage,
    WeeklyDailyDetail,
    WeeklyLeaveDetail,
    WeeklyStaffing,
)
from staffing_tool.schedule_import import (
    DailyDetailDay,
    aggregate_week_from_records,
    find_schedule_workbook_for_week,
    parse_ops_view_daily,
    parse_schedule_workbook,
)

EM = "\u2014"
BASE_ORDER = ["Bedford", "Lawrence", "Manchester", "Mansfield", "Plymouth"]
DAILY_CREW_TARGET = REQUIRED_DAY // 7 + REQUIRED_NIGHT // 7  # 12

ROLE_LABELS = {
    "RN": "RN",
    "Medic": "Paramedic",
    "EMT": "EMT",
    "Pilot": "Pilot",
}


@dataclass
class WeeklyReportContext:
    week_start: str
    week_of: str
    week_dates: str
    prepared_date: str
    kpi_data: list[tuple[str, str]]
    daily_data: list[tuple[str, str, str, str, str]]
    daily_totals: tuple[str, str, str, str]  # filled/target, rw, gr, exceptions
    base_coverage: list[tuple[str, str, str, str, str]]
    leave_breakdown: list[tuple[str, int]]
    ot_by_role: list[tuple[str, int, int]]  # label, day, night
    ot_total_day: int
    ot_total_night: int
    exception_by_role: list[tuple[str, int, int, int, int, int, int]]  # label + 6 cols
    exception_col_totals: tuple[int, int, int, int, int, int]
    trend_data: list[tuple[str, float, float, float]] = field(default_factory=list)


def _pct(v: float) -> str:
    return f"{100 * v:.1f}%"


def _short_label(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    return d.strftime("%b ") + str(d.day)


def _exc_count(
    breakdown: dict[tuple[str, str], int],
    role: str,
    keys: list[str],
) -> int:
    return sum(breakdown.get((role, k), 0) for k in keys)


def _schedule_search_dirs(db_path: str) -> list[str]:
    root = Path(db_path).resolve().parent
    return [str(root / "uploads"), str(root / "archive")]


def _persist_daily_detail_rows(
    session,
    week_start: str,
    daily_detail: list[DailyDetailDay],
) -> None:
    session.query(WeeklyDailyDetail).filter(
        WeeklyDailyDetail.week_start == week_start
    ).delete()
    for day in daily_detail:
        session.add(
            WeeklyDailyDetail(
                week_start=week_start,
                day_date=day.day_date.isoformat(),
                filled=day.filled,
                rw=day.rw,
                gr=day.gr,
                exceptions=day.exceptions,
            )
        )


def _backfill_daily_detail(session, week_start: str, db_path: str) -> bool:
    """Try to rebuild daily detail from a schedule workbook on disk."""
    path = find_schedule_workbook_for_week(week_start, _schedule_search_dirs(db_path))
    if not path:
        return False
    mgr = frozenset(manager_last_names_upper_from_session(session))
    records, _issues, ops_coverage = parse_schedule_workbook(
        path,
        week_start=week_start,
        manager_last_names_upper=mgr,
    )
    if not records:
        return False
    ops_daily = (
        parse_ops_view_daily(path, week_start) if ops_coverage is not None else None
    )
    agg = aggregate_week_from_records(
        week_start,
        records,
        ops_coverage=ops_coverage,
        ops_daily=ops_daily,
    )
    if not agg.daily_detail:
        return False
    _persist_daily_detail_rows(session, week_start, agg.daily_detail)
    session.flush()
    return True


def _daily_row_tuple(day_date: date, filled: int, rw: int, gr: int, exc: int) -> tuple[str, str, str, str, str]:
    label = f"{day_date.strftime('%A')} {day_date.month}/{day_date.day}"
    return (
        label,
        f"{filled} / {DAILY_CREW_TARGET}",
        str(rw),
        str(gr),
        str(exc),
    )


def _load_daily_data(
    session,
    week_start: str,
    db_path: str,
) -> list[tuple[str, str, str, str, str]]:
    rows = (
        session.query(WeeklyDailyDetail)
        .filter(WeeklyDailyDetail.week_start == week_start)
        .order_by(WeeklyDailyDetail.day_date)
        .all()
    )
    if not rows:
        if _backfill_daily_detail(session, week_start, db_path):
            rows = (
                session.query(WeeklyDailyDetail)
                .filter(WeeklyDailyDetail.week_start == week_start)
                .order_by(WeeklyDailyDetail.day_date)
                .all()
            )
    if rows:
        return [
            _daily_row_tuple(
                datetime.strptime(r.day_date, "%Y-%m-%d").date(),
                int(r.filled),
                int(r.rw),
                int(r.gr),
                int(r.exceptions),
            )
            for r in rows
        ]

    start = datetime.strptime(week_start, "%Y-%m-%d").date()
    placeholder = f"{EM} / {DAILY_CREW_TARGET}"
    return [
        (
            f"{(start + timedelta(days=i)).strftime('%A')} "
            f"{(start + timedelta(days=i)).month}/{(start + timedelta(days=i)).day}",
            placeholder,
            EM,
            EM,
            EM,
        )
        for i in range(7)
    ]


def _week_display(iso: str) -> tuple[str, str]:
    start = datetime.strptime(iso, "%Y-%m-%d").date()
    end = start + timedelta(days=6)
    week_of = start.strftime("%B %d, %Y")
    if start.year == end.year:
        week_dates = f"{start.strftime('%b %d')} \u2013 {end.strftime('%b %d, %Y')}"
    else:
        week_dates = f"{start.strftime('%b %d, %Y')} \u2013 {end.strftime('%b %d, %Y')}"
    return week_of, week_dates


def list_week_starts(db_path: str) -> list[str]:
    with session_scope(db_path) as session:
        rows = (
            session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start.desc())
            .all()
        )
        return [r[0] for r in rows]


def load_week_report_data(db_path: str, week_start: str) -> WeeklyReportContext:
    with session_scope(db_path) as session:
        row = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start == week_start)
            .first()
        )
        if not row:
            raise ValueError(f"No staffing data for week starting {week_start}.")

        coverages = (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start == week_start)
            .all()
        )
        base_configs = session.query(BaseConfig).all()
        metrics = compute_week_metrics(row, coverages, base_configs)
        ws = cast(Any, row)

        leave_breakdown = [
            ("AT", int(ws.leave_at)),
            ("LT", int(ws.leave_lt)),
            ("SICK", int(ws.leave_sick)),
            ("LOA", int(ws.leave_loa) + int(ws.leave_pfml or 0)),
            ("JURY", int(ws.leave_jury)),
            ("BREV", int(ws.leave_brev)),
        ]

        leave_details = (
            session.query(WeeklyLeaveDetail)
            .filter(WeeklyLeaveDetail.week_start == week_start)
            .all()
        )
        detail_breakdown = {
            (r.role, r.leave_type): int(r.count) for r in leave_details
        }
        exception_by_role: list[tuple[str, int, int, int, int, int, int]] = []
        for role in EXCEPTION_GRID_ROLES:
            vals = tuple(
                _exc_count(detail_breakdown, role, EXCEPTION_COL_BREAKDOWN_KEYS[col])
                for col in EXCEPTION_GRID_COLS
            )
            exception_by_role.append((ROLE_LABELS.get(role, role), *vals))
        exception_col_totals = tuple(
            sum(
                _exc_count(detail_breakdown, r, EXCEPTION_COL_BREAKDOWN_KEYS[col])
                for r in EXCEPTION_GRID_ROLES
            )
            for col in EXCEPTION_GRID_COLS
        )

        ot_by_role = [
            (ROLE_LABELS["RN"], int(ws.ot_rn_day or 0), int(ws.ot_rn_night or 0)),
            (ROLE_LABELS["Medic"], int(ws.ot_medic_day or 0), int(ws.ot_medic_night or 0)),
            (ROLE_LABELS["EMT"], int(ws.ot_emt_day or 0), int(ws.ot_emt_night or 0)),
        ]
        ot_total_day = sum(day for _, day, _ in ot_by_role)
        ot_total_night = sum(night for _, _, night in ot_by_role)

        cfg_by_name = {b.base_name: b for b in base_configs}
        base_coverage: list[tuple[str, str, str, str, str]] = []
        bm = metrics.base_metrics or {}
        for base in BASE_ORDER:
            if base not in bm and base not in cfg_by_name:
                continue
            m = bm.get(base, {})
            cfg = cfg_by_name.get(base)
            rw_cap = int(cfg.rw_total_unit_days) if cfg else 0
            gr_cap = int(cfg.gr_total_unit_days) if cfg else 0
            rw_n = int(m.get("rw_staffed", 0))
            gr_n = int(m.get("gr_staffed", 0))
            base_coverage.append((
                base,
                str(rw_n) if rw_n else EM,
                _pct(m["rw_pct"]) if rw_cap and rw_n else (EM if not rw_cap else "0.0%"),
                str(gr_n) if gr_n else EM,
                _pct(m["gr_pct"]) if gr_cap and gr_n else (EM if not gr_cap else "0.0%"),
            ))

        trend_weeks = (
            session.query(WeeklyStaffing.week_start)
            .filter(WeeklyStaffing.week_start <= week_start)
            .order_by(WeeklyStaffing.week_start.desc())
            .limit(8)
            .all()
        )
        trend_starts = [w[0] for w in reversed(trend_weeks)]
        trend_data: list[tuple[str, float, float, float]] = []
        for ws_iso in trend_starts:
            wrow = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == ws_iso)
                .first()
            )
            if not wrow:
                continue
            wc = (
                session.query(WeeklyBaseCoverage)
                .filter(WeeklyBaseCoverage.week_start == ws_iso)
                .all()
            )
            wm = compute_week_metrics(wrow, wc, base_configs)
            trend_data.append((
                _short_label(ws_iso),
                wm.staffing_rate * 100,
                wm.ot_dependency * 100,
                wm.leave_exposure * 100,
            ))

        week_of, week_dates = _week_display(week_start)
        daily_data = _load_daily_data(session, week_start, db_path)

        rw_total = str(int(metrics.rw_staffed_unit_days))
        gr_total = str(int(metrics.gr_staffed_unit_days))
        exc_total = str(metrics.leave_total)

        return WeeklyReportContext(
            week_start=week_start,
            week_of=week_of,
            week_dates=week_dates,
            prepared_date=date.today().strftime("%B %d, %Y"),
            kpi_data=[
                ("Staffing Rate", _pct(metrics.staffing_rate)),
                ("OT Dependency", _pct(metrics.ot_dependency)),
                ("Shift Exception %", _pct(metrics.leave_exposure)),
                ("System RW %", _pct(metrics.system_rw_pct)),
                ("System GR %", _pct(metrics.system_gr_pct)),
                ("Person-Shifts", str(TOTAL_PERSON_SHIFTS)),
            ],
            daily_data=daily_data,
            daily_totals=(
                f"{metrics.filled_total} / {REQUIRED_TOTAL}",
                rw_total,
                gr_total,
                exc_total,
            ),
            base_coverage=base_coverage,
            leave_breakdown=leave_breakdown,
            ot_by_role=ot_by_role,
            ot_total_day=ot_total_day,
            ot_total_night=ot_total_night,
            exception_by_role=exception_by_role,
            exception_col_totals=exception_col_totals,
            trend_data=trend_data,
        )


def _leave_rows(ctx: WeeklyReportContext):
    total = sum(c for _, c in ctx.leave_breakdown)
    rows = []
    for code, count in ctx.leave_breakdown:
        pct = f"{100 * count / total:.1f}%" if total else EM
        rows.append((code, count, pct))
    return rows, total


def _leave_top2(ctx: WeeklyReportContext) -> set[str]:
    ranked = sorted(ctx.leave_breakdown, key=lambda r: r[1], reverse=True)
    return {code for code, count in ranked[:2] if count > 0}


def _daily_table(ctx: WeeklyReportContext):
    headers = ["Day", "Filled / Target", "RW", "GR", "Exceptions"]
    col_w = style.full_width_col_widths([1.6, 1.2, 0.9, 0.9, 1.0])
    rows = [headers] + [list(r) for r in ctx.daily_data]
    ft, rw, gr, exc = ctx.daily_totals
    rows.append(["Week Total", ft, rw, gr, exc])
    total_row = len(rows) - 1
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), style.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), style.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), style.F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), style.F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, total_row - 1), [style.WHITE, style.LGRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, style.MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (4, -1), 'CENTER'),
        ('BACKGROUND', (0, total_row), (-1, total_row), style.MGRAY),
        ('FONTNAME', (0, total_row), (-1, total_row), style.F('IBMPlexMonoBold')),
    ] + style.num_style_cells([2, 3, 4])))
    return t


def _base_coverage_table(ctx: WeeklyReportContext):
    headers = ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"]
    col_w = style.full_width_col_widths([1.5, 1.25, 1.25, 1.25, 2.25])
    rows = [headers] + [list(r) for r in ctx.base_coverage]
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), style.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), style.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), style.F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), style.F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [style.WHITE, style.LGRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, style.MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ] + style.num_style_cells([1, 2, 3, 4])))
    return t


def _exception_table(ctx: WeeklyReportContext):
    headers = ["Exception Type", "Count", "% of Total"]
    col_w = style.full_width_col_widths([4.0, 1.5, 2.0])
    leave_rows, total = _leave_rows(ctx)
    rows = [headers] + [[code, str(count), pct] for code, count, pct in leave_rows]
    rows.append(["Total", str(total), "100%" if total else EM])
    total_row = len(rows) - 1
    top2 = _leave_top2(ctx)
    red_rules = []
    for i, (code, _count, _pct) in enumerate(leave_rows, start=1):
        if code in top2:
            red_rules += [
                ('TEXTCOLOR', (1, i), (2, i), style.RED),
                ('FONTNAME', (1, i), (2, i), style.F('IBMPlexMonoBold')),
            ]
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), style.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), style.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), style.F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), style.F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, total_row - 1), [style.WHITE, style.LGRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, style.MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (2, -1), 'CENTER'),
        ('BACKGROUND', (0, total_row), (-1, total_row), style.MGRAY),
        ('FONTNAME', (0, total_row), (-1, total_row), style.F('IBMPlexMonoBold')),
    ] + style.num_style_cells([1, 2]) + red_rules))
    return t


def _ot_by_role_table(ctx: WeeklyReportContext):
    headers = ["Role", "Day", "Night", "Total"]
    col_w = style.full_width_col_widths([2.5, 1.25, 1.25, 1.25])
    rows = [headers]
    for label, day, night in ctx.ot_by_role:
        rows.append([label, str(day), str(night), str(day + night)])
    rows.append([
        "Total",
        str(ctx.ot_total_day),
        str(ctx.ot_total_night),
        str(ctx.ot_total_day + ctx.ot_total_night),
    ])
    total_row = len(rows) - 1
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), style.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), style.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), style.F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), style.F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, total_row - 1), [style.WHITE, style.LGRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, style.MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, total_row), (-1, total_row), style.MGRAY),
        ('FONTNAME', (0, total_row), (-1, total_row), style.F('IBMPlexMonoBold')),
    ] + style.num_style_cells([1, 2, 3])))
    return t


def _exception_by_role_table(ctx: WeeklyReportContext):
    headers = ["Role", *EXCEPTION_GRID_COLS]
    col_w = style.full_width_col_widths([1.35] + [0.85] * len(EXCEPTION_GRID_COLS))
    rows = [headers]
    for label, *vals in ctx.exception_by_role:
        rows.append([label, *[str(v) for v in vals]])
    rows.append(["Total", *[str(v) for v in ctx.exception_col_totals]])
    total_row = len(rows) - 1
    t = Table(rows, colWidths=col_w)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), style.NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), style.WHITE),
        ('FONTNAME', (0, 0), (-1, 0), style.F('BarlowBold')),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('FONTNAME', (0, 1), (-1, -1), style.F('BarlowRegular')),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, total_row - 1), [style.WHITE, style.LGRAY]),
        ('GRID', (0, 0), (-1, -1), 0.5, style.MGRAY),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('BACKGROUND', (0, total_row), (-1, total_row), style.MGRAY),
        ('FONTNAME', (0, total_row), (-1, total_row), style.F('IBMPlexMonoBold')),
    ] + style.num_style_cells(list(range(1, len(headers))))))
    return t


def _fig_to_png_base64(fig) -> str:
    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode('ascii')


def _build_trend_fig(ctx: WeeklyReportContext):
    labels = [r[0] for r in ctx.trend_data]
    staffing = [r[1] for r in ctx.trend_data]
    ot_dep = [r[2] for r in ctx.trend_data]
    exc_pct = [r[3] for r in ctx.trend_data]
    x = range(len(labels))

    fig, ax1 = plt.subplots(figsize=(7.5, 2.4))
    fig.patch.set_facecolor('white')
    ax1.set_facecolor('white')
    ax1.bar(x, exc_pct, color=style.C_MGRAY, width=0.55, alpha=0.55,
            label='Exception % (left)', zorder=1)
    ax1.plot(x, staffing, color=style.C_BLUE, linewidth=2, marker='o', markersize=4,
             label='Staffing Rate % (left)', zorder=3)
    ax1.set_ylabel('Staffing / Exception %', fontsize=7, color='#333333')
    ax1.set_ylim(0, 110)
    ax1.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))

    ax2 = ax1.twinx()
    ax2.plot(x, ot_dep, color=style.C_RED, linewidth=1.5, marker='s', markersize=3,
             linestyle='--', label='OT Dependency % (right)', zorder=3)
    ax2.set_ylabel('OT Dependency %', fontsize=7, color=style.C_RED)
    ax2.set_ylim(0, 30)
    ax2.yaxis.set_major_formatter(mticker.FormatStrFormatter('%.0f%%'))
    ax2.spines['right'].set_color(style.C_RED)
    ax2.tick_params(axis='y', colors=style.C_RED, labelsize=7)

    ax1.set_xticks(list(x))
    ax1.set_xticklabels(labels, fontsize=7)
    ax1.spines['top'].set_visible(False)
    ax1.spines['left'].set_color(style.C_MGRAY)
    ax1.spines['bottom'].set_color(style.C_MGRAY)
    ax1.tick_params(colors='#333333', labelsize=7)
    ax1.yaxis.grid(True, color=style.C_MGRAY, linewidth=0.5, linestyle='--')
    ax1.set_axisbelow(True)

    style.apply_below_chart_legend(fig, ax1, ax2)
    fig.tight_layout(pad=0.3, rect=(0, 0.10, 1, 1))
    return fig


def _trend_chart(ctx: WeeklyReportContext):
    return style.chart_to_image(_build_trend_fig(ctx), style.USABLE_W)


def _build_exception_bar_fig(ctx: WeeklyReportContext):
    codes = [code for code, _ in ctx.leave_breakdown]
    counts = [count for _, count in ctx.leave_breakdown]
    top2 = _leave_top2(ctx)
    bar_colors = [style.C_RED if code in top2 else style.C_BLUE for code in codes]

    fig, ax = style.base_figure(7.5, 1.8)
    y = range(len(codes))
    ax.barh(list(y), counts, color=bar_colors, height=0.5)
    ax.set_yticks(list(y))
    ax.set_yticklabels(codes, fontsize=7)
    ax.set_xlabel('Shift exceptions (count)', fontsize=7, color='#333333')
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.xaxis.grid(True, color=style.C_MGRAY, linewidth=0.5, linestyle='--')
    ax.set_axisbelow(True)
    for i, v in enumerate(counts):
        if v:
            ax.text(v + 0.1, i, str(v), va='center', fontsize=7, color='#333333')
    fig.tight_layout(pad=0.4)
    return fig


def _exception_bar_chart(ctx: WeeklyReportContext):
    return style.chart_to_image(_build_exception_bar_fig(ctx), style.USABLE_W, 1.8 * inch)


def build_pdf(ctx: WeeklyReportContext, output_path: str) -> str:
    style.register_fonts()
    running_header = f"Weekly Staffing Report \u2014 Week of {ctx.week_of}"
    on_first, on_later = style.make_page_callbacks(
        footer_short_title="Weekly Staffing",
        running_header_title=running_header,
    )

    doc = SimpleDocTemplate(
        output_path,
        pagesize=style.PAGE_SIZE,
        leftMargin=style.MARGIN,
        rightMargin=style.MARGIN,
        topMargin=style.MARGIN,
        bottomMargin=style.MARGIN,
    )

    story = [
        style.title_banner(
            "WEEKLY STAFFING REPORT",
            f"Week of {ctx.week_of}  |  Coverage Period: {ctx.week_dates}",
            meta_line=f"Prepared {ctx.prepared_date} \u00b7 CONFIDENTIAL",
        ),
        Spacer(1, 10),
        style.section_bar("KEY PERFORMANCE INDICATORS"),
        style.kpi_row(ctx.kpi_data),
        Spacer(1, 10),
        style.section_bar("8-WEEK STAFFING TREND"),
        _trend_chart(ctx),
        Spacer(1, 10),
    ]

    story.append(style.section_bar("EXCEPTION BREAKDOWN THIS WEEK"))
    story.append(_exception_bar_chart(ctx))
    story.append(Spacer(1, 8))
    story.append(style.section_bar("SCHEDULE EXCEPTIONS"))
    story.append(_exception_table(ctx))
    story.append(Spacer(1, 10))
    story.append(style.section_bar("OVERTIME BY ROLE"))
    story.append(_ot_by_role_table(ctx))
    story.append(Spacer(1, 10))
    story.append(style.section_bar("SCHEDULE EXCEPTIONS BY ROLE"))
    story.append(_exception_by_role_table(ctx))
    story.append(Spacer(1, 10))
    story.append(style.section_bar("DAILY DETAIL"))
    story.append(_daily_table(ctx))
    story.append(Spacer(1, 10))
    story.append(style.section_bar("COVERAGE BY BASE"))
    story.append(_base_coverage_table(ctx))
    story.append(Spacer(1, 10))

    doc.build(story, onFirstPage=on_first, onLaterPages=on_later,
              canvasmaker=style.NumberedCanvas)
    return output_path


def _html_section_bar(title: str, navy: str) -> str:
    return (
        f'<tr><td style="background:{navy};color:#ffffff;padding:8px 24px;'
        f'font-size:11px;font-weight:bold;letter-spacing:0.5px;">{title}</td></tr>'
    )


def _html_data_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    navy: str,
    lgray: str,
    mgray: str,
    right_cols: set[int] | None = None,
    total_row: bool = False,
) -> str:
    right_cols = right_cols or set()
    th = ''.join(
        f'<th style="padding:6px 8px;text-align:{"right" if i in right_cols else "left"};">{h}</th>'
        for i, h in enumerate(headers)
    )
    body = ''
    for ri, row in enumerate(rows):
        is_total = total_row and ri == len(rows) - 1
        bg = mgray if is_total else (lgray if ri % 2 else '#ffffff')
        fw = 'font-weight:bold;' if is_total else ''
        cells = ''
        for ci, cell in enumerate(row):
            align = 'right' if ci in right_cols else ('left' if ci == 0 else 'center')
            cells += (
                f'<td style="padding:6px 8px;text-align:{align};border:1px solid {mgray};{fw}">'
                f'{cell}</td>'
            )
        body += f'<tr style="background:{bg};">{cells}</tr>'
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="font-size:12px;border-collapse:collapse;">'
        f'<tr style="background:{navy};color:#ffffff;">{th}</tr>{body}</table>'
    )


def build_html(ctx: WeeklyReportContext, output_path: str) -> str:
    leave_rows, total = _leave_rows(ctx)
    top2 = _leave_top2(ctx)
    navy, blue, red, lgray, mgray = '#052C47', '#2A4492', '#C12126', '#E6E6E6', '#CBC7D1'

    trend_b64 = _fig_to_png_base64(_build_trend_fig(ctx)) if ctx.trend_data else ''
    exc_chart_b64 = _fig_to_png_base64(_build_exception_bar_fig(ctx))

    kpi_cells = ''.join(
        f'<td style="padding:8px 4px;text-align:center;border:1px solid {mgray};">'
        f'<div style="font-size:18px;font-weight:bold;color:{navy};">{val}</div>'
        f'<div style="font-size:11px;color:#333;">{label}</div></td>'
        for label, val in ctx.kpi_data
    )

    max_count = max((c for _, c in ctx.leave_breakdown), default=1) or 1
    exc_rows = ''
    for code, count, pct in leave_rows:
        color = red if code in top2 else blue
        bar_w = int(100 * count / max_count) if count else 0
        exc_rows += (
            f'<tr><td style="padding:6px 8px;font-weight:bold;border:1px solid {mgray};">{code}</td>'
            f'<td style="padding:6px 4px;text-align:right;border:1px solid {mgray};">{count}</td>'
            f'<td style="padding:6px 4px;text-align:right;border:1px solid {mgray};">{pct}</td>'
            f'<td style="padding:6px 8px;border:1px solid {mgray};">'
            f'<div style="background:{lgray};height:14px;border-radius:2px;">'
            f'<div style="background:{color};width:{bar_w}%;height:14px;"></div>'
            f'</div></td></tr>'
        )

    grid_label = ' &middot; '.join(EXCEPTION_GRID_COLS)

    daily_rows = [list(r) for r in ctx.daily_data]
    ft, rw, gr, exc = ctx.daily_totals
    daily_rows.append(['Week Total', ft, rw, gr, exc])
    has_daily_detail = any(
        not row[1].startswith(EM) for row in ctx.daily_data
    )
    daily_detail_note = (
        ''
        if has_daily_detail
        else (
            '<p style="font-size:11px;color:#555;margin:10px 0 0;">'
            'Per-day detail is filled when you import the schedule for this week. '
            'Re-import from <strong>Import schedule</strong> if daily rows are blank.'
            '</p>'
        )
    )
    daily_table = _html_data_table(
        ['Day', 'Filled / Target', 'RW', 'GR', 'Exceptions'],
        daily_rows,
        navy=navy, lgray=lgray, mgray=mgray,
        right_cols={1, 2, 3, 4},
        total_row=True,
    )

    base_table = _html_data_table(
        ['Base', 'RW Shifts', 'RW Avail %', 'GR Shifts', 'GR Avail %'],
        [list(r) for r in ctx.base_coverage],
        navy=navy, lgray=lgray, mgray=mgray,
        right_cols={1, 2, 3, 4},
    )

    ot_rows = [
        [label, str(day), str(night), str(day + night)]
        for label, day, night in ctx.ot_by_role
    ]
    ot_rows.append([
        'Total',
        str(ctx.ot_total_day),
        str(ctx.ot_total_night),
        str(ctx.ot_total_day + ctx.ot_total_night),
    ])
    ot_table = _html_data_table(
        ['Role', 'Day', 'Night', 'Total'],
        ot_rows,
        navy=navy, lgray=lgray, mgray=mgray,
        right_cols={1, 2, 3},
        total_row=True,
    )

    exc_role_rows = [
        [label, *[str(v) for v in vals]]
        for label, *vals in ctx.exception_by_role
    ]
    exc_role_rows.append(['Total', *[str(v) for v in ctx.exception_col_totals]])
    exc_role_table = _html_data_table(
        ['Role', *EXCEPTION_GRID_COLS],
        exc_role_rows,
        navy=navy, lgray=lgray, mgray=mgray,
        right_cols=set(range(1, len(EXCEPTION_GRID_COLS) + 1)),
        total_row=True,
    )

    top2_note = ', '.join(
        f'{code} ({next(c for cd, c in ctx.leave_breakdown if cd == code)})'
        for code in sorted(top2, key=lambda c: next(x for cd, x in ctx.leave_breakdown if cd == c), reverse=True)
    )

    trend_section = ''
    if trend_b64:
        trend_section = (
            _html_section_bar('8-WEEK STAFFING TREND', navy)
            + f'<tr><td style="padding:12px 16px;">'
            f'<img src="data:image/png;base64,{trend_b64}" alt="8-week staffing trend" '
            f'style="width:100%;max-width:568px;height:auto;display:block;" />'
            f'</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekly Staffing Report — {ctx.week_of}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;">

<tr><td style="background:{navy};color:#ffffff;padding:20px 24px;">
<div style="font-size:20px;font-weight:bold;letter-spacing:0.5px;">WEEKLY STAFFING REPORT</div>
<div style="font-size:13px;color:{lgray};margin-top:6px;">Week of {ctx.week_of} &nbsp;|&nbsp; {ctx.week_dates}</div>
<div style="font-size:11px;color:{mgray};margin-top:8px;">Prepared {ctx.prepared_date} &middot; CONFIDENTIAL</div>
<div style="font-size:10px;color:#ffffff;margin-top:12px;font-weight:bold;">BOSTON MEDFLIGHT</div>
<div style="font-size:10px;color:{lgray};">CLINICAL OPERATIONS</div>
</td></tr>

{_html_section_bar('KEY PERFORMANCE INDICATORS', navy)}
<tr><td style="padding:12px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>{kpi_cells}</tr></table>
</td></tr>

{trend_section}

{_html_section_bar(f'EXCEPTION BREAKDOWN THIS WEEK ({grid_label})', navy)}
<tr><td style="padding:12px 16px;">
<img src="data:image/png;base64,{exc_chart_b64}" alt="Exception breakdown" style="width:100%;max-width:568px;height:auto;display:block;margin-bottom:12px;" />
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-size:12px;border-collapse:collapse;">
<tr style="background:{lgray};">
<th align="left" style="padding:6px 8px;border:1px solid {mgray};">Type</th>
<th align="right" style="padding:6px 4px;border:1px solid {mgray};">Count</th>
<th align="right" style="padding:6px 4px;border:1px solid {mgray};">%</th>
<th align="left" style="padding:6px 8px;border:1px solid {mgray};">Share</th>
</tr>
{exc_rows}
<tr style="background:{mgray};font-weight:bold;">
<td style="padding:6px 8px;border:1px solid {mgray};">Total</td>
<td align="right" style="padding:6px 4px;border:1px solid {mgray};">{total}</td>
<td align="right" style="padding:6px 4px;border:1px solid {mgray};">{'100%' if total else EM}</td>
<td style="border:1px solid {mgray};"></td>
</tr>
</table>
<p style="font-size:11px;color:#555;margin:10px 0 0;">Top drivers (red in chart): {top2_note or 'n/a'}.</p>
</td></tr>

{_html_section_bar('OVERTIME BY ROLE', navy)}
<tr><td style="padding:12px 16px;">{ot_table}
<p style="font-size:11px;color:#555;margin:10px 0 0;">OT shift counts by RN, Paramedic, and EMT (day / night).</p>
</td></tr>

{_html_section_bar('SCHEDULE EXCEPTIONS BY ROLE', navy)}
<tr><td style="padding:12px 16px;">{exc_role_table}
<p style="font-size:11px;color:#555;margin:10px 0 0;">Shift counts by role and exception type (AT &middot; LT &middot; SICK &middot; LOA &middot; JURY &middot; BREV).</p>
</td></tr>

{_html_section_bar('DAILY DETAIL', navy)}
<tr><td style="padding:12px 16px;">{daily_table}
{daily_detail_note}
</td></tr>

{_html_section_bar('COVERAGE BY BASE', navy)}
<tr><td style="padding:12px 16px;">{base_table}</td></tr>

<tr><td style="padding:16px 24px;font-size:11px;color:#666;border-top:1px solid {mgray};">
Boston MedFlight &middot; Clinical Operations &middot; Confidential
</td></tr>

</table></td></tr></table>
</body></html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path


def _output_paths(output_dir: str, week_start: str) -> tuple[str, str]:
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.join(output_dir, f"BMF_Weekly_Staffing_{week_start}")
    return f"{base}.pdf", f"{base}.html"


def export_weekly_staffing_pdf(db_path: str, week_start: str, output_dir: str) -> str:
    ctx = load_week_report_data(db_path, week_start)
    pdf_path, _ = _output_paths(output_dir, week_start)
    return build_pdf(ctx, pdf_path)


def export_weekly_staffing_html(db_path: str, week_start: str, output_dir: str) -> str:
    ctx = load_week_report_data(db_path, week_start)
    _, html_path = _output_paths(output_dir, week_start)
    return build_html(ctx, html_path)


def export_weekly_staffing_both(db_path: str, week_start: str, output_dir: str) -> tuple[str, str]:
    ctx = load_week_report_data(db_path, week_start)
    pdf_path, html_path = _output_paths(output_dir, week_start)
    build_pdf(ctx, pdf_path)
    build_html(ctx, html_path)
    return pdf_path, html_path
