"""KPI data-quality checks across weeks in staffing.db."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from .metrics import compute_week_metrics
from .models import BaseConfig, WeeklyBaseCoverage, WeeklyLeaveDetail, WeeklyStaffing
from .report import _leave_totals_from_breakdown


def _ot_day_night_total(row: WeeklyStaffing) -> int:
    return (
        int(row.ot_rn_day or 0)
        + int(row.ot_rn_night or 0)
        + int(row.ot_medic_day or 0)
        + int(row.ot_medic_night or 0)
        + int(row.ot_emt_day or 0)
        + int(row.ot_emt_night or 0)
    )


def audit_kpi_data_quality(session: Session) -> dict[str, object]:
    """
    Compare stored weekly totals vs exception grid and OT components.

    Returns a summary dict for the Settings health panel.
    """
    configs = session.query(BaseConfig).all()
    weeks = session.query(WeeklyStaffing).order_by(WeeklyStaffing.week_start).all()
    leave_mismatches: list[str] = []
    ot_mismatches: list[str] = []
    legacy_pfml: list[str] = []

    # Load coverage and leave detail for all weeks once, then group in memory,
    # rather than running two SELECTs per week.
    cov_by_week: dict[str, list[WeeklyBaseCoverage]] = defaultdict(list)
    for cov_row in session.query(WeeklyBaseCoverage).all():
        cov_by_week[cov_row.week_start].append(cov_row)
    leave_by_week: dict[str, list[WeeklyLeaveDetail]] = defaultdict(list)
    for leave_row in session.query(WeeklyLeaveDetail).all():
        leave_by_week[leave_row.week_start].append(leave_row)

    for ws in weeks:
        week = ws.week_start
        cov = cov_by_week.get(week, [])
        metrics = compute_week_metrics(ws, cov, configs)

        details = leave_by_week.get(week, [])
        if details:
            bd = {(d.role, d.leave_type): d.count for d in details}
            grid_total, _ = _leave_totals_from_breakdown(bd)
            if grid_total != metrics.leave_total:
                leave_mismatches.append(
                    f"{week}: grid={grid_total} stored={metrics.leave_total}"
                )

        ot_dn = _ot_day_night_total(ws)
        ot_legacy = int(ws.ot_rn or 0) + int(ws.ot_medic or 0) + int(ws.ot_emt or 0)
        if ot_dn > 0 and ot_legacy > 0 and ot_dn != ot_legacy:
            ot_mismatches.append(f"{week}: day/night={ot_dn} legacy={ot_legacy}")
        elif ot_dn > 0 and ot_dn != metrics.ot_shifts:
            ot_mismatches.append(
                f"{week}: day/night={ot_dn} metric={metrics.ot_shifts}"
            )

        if (ws.leave_pfml or 0) > 0:
            legacy_pfml.append(week)

    issue_count = len(leave_mismatches) + len(ot_mismatches) + len(legacy_pfml)
    return {
        "weeks_checked": len(weeks),
        "leave_mismatch_count": len(leave_mismatches),
        "leave_mismatch_samples": leave_mismatches[:5],
        "ot_mismatch_count": len(ot_mismatches),
        "ot_mismatch_samples": ot_mismatches[:5],
        "legacy_pfml_count": len(legacy_pfml),
        "legacy_pfml_samples": legacy_pfml[:5],
        "issue_count": issue_count,
        "all_ok": issue_count == 0,
    }
