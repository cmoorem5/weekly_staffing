"""Week loading, metric prep, and status text for the board-pack workbook.

Status wording is "On target / Monitor / Action needed" — never
Green/Yellow/Red in user-facing cells (docs/report-generator-spec.md).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .leave_grid import (
    EXCEPTION_COL_KEYS,
    EXCEPTION_GRID_COLS,
    EXCEPTION_GRID_ROLES,
)
from .metrics import (
    BASE_DISPLAY_ORDER,
    WeekMetrics,
    compute_week_metrics,
)
from .models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyStaffing,
)
from .rag import RAG, evaluate_rag

# §1.2 — which base/unit/shift cells exist (False → render "N/A")
BASE_UNIT_CELL_CONFIGURED: dict[str, dict[str, bool]] = {
    "Bedford": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": True},
    "Lawrence": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": False},
    "Manchester": {"rw_d": True, "rw_n": False, "gr_d": False, "gr_n": False},
    "Mansfield": {"rw_d": True, "rw_n": False, "gr_d": True, "gr_n": False},
    "Plymouth": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": False},
}


# Weekly Staffing Detail: fixed base order (canonical order lives in metrics).
DETAIL_BASE_ORDER = BASE_DISPLAY_ORDER

# Backward-compatible names (canonical definitions in leave_grid)
LEAVE_TYPE_COLS = EXCEPTION_GRID_COLS
EXCEPTION_ROLES = EXCEPTION_GRID_ROLES


def _exc_count_breakdown(breakdown: dict, role: str, keys: list[str]) -> int:
    return sum(breakdown.get((role, k), 0) for k in keys)


def _leave_totals_from_breakdown(breakdown: dict) -> tuple[int, list[int]]:
    """Sum shift exceptions from grid: (grand_total, [AT, LT, SICK, LOA, JURY, BREV])."""
    col_totals = []
    for keys in EXCEPTION_COL_KEYS:
        col_totals.append(
            sum(_exc_count_breakdown(breakdown, r, keys) for r in EXCEPTION_ROLES)
        )
    return sum(col_totals), col_totals


def _parse_week(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _week_end(week_start: str) -> str:
    d = _parse_week(week_start)
    end = d + timedelta(days=6)
    return end.strftime("%Y-%m-%d")


def _load_week_with_coverage(
    session: Session, week_start: str
) -> tuple[WeeklyStaffing, list[WeeklyBaseCoverage], list[BaseConfig]] | None:
    row = (
        session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start == week_start)
        .first()
    )
    if not row:
        return None
    bases = session.query(BaseConfig).order_by(BaseConfig.base_name).all()
    coverages = (
        session.query(WeeklyBaseCoverage)
        .filter(WeeklyBaseCoverage.week_start == week_start)
        .all()
    )
    return (row, coverages, bases)


def _load_weeks_ordered(
    session: Session, n: int, through_week_start: str | None = None
) -> list[str]:
    """Return n week_start values, ordered oldest to newest. If through_week_start is set, return the n weeks ending at that week (inclusive)."""
    q = session.query(WeeklyStaffing.week_start)
    if through_week_start:
        q = q.filter(WeeklyStaffing.week_start <= through_week_start)
    rows = q.order_by(WeeklyStaffing.week_start.desc()).limit(n).all()
    starts = [r[0] for r in reversed(rows)]
    return starts


def _metrics_for_weeks(
    session: Session, week_starts: list[str]
) -> list[tuple[str, WeekMetrics, RAG | None]]:
    """Load metrics for each week and RAG for board metrics (using first metric only for status)."""
    if not week_starts:
        return []
    thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
    bases = session.query(BaseConfig).order_by(BaseConfig.base_name).all()
    rows_by_week = {
        r.week_start: r
        for r in session.query(WeeklyStaffing)
        .filter(WeeklyStaffing.week_start.in_(week_starts))
        .all()
    }
    coverages_by_week: dict[str, list[WeeklyBaseCoverage]] = defaultdict(list)
    for c in (
        session.query(WeeklyBaseCoverage)
        .filter(WeeklyBaseCoverage.week_start.in_(week_starts))
        .all()
    ):
        coverages_by_week[c.week_start].append(c)
    result = []
    for ws in week_starts:
        row = rows_by_week.get(ws)
        if row is None:
            continue
        m = compute_week_metrics(row, coverages_by_week.get(ws, []), bases)
        rag = None
        if thresholds.get("Staffing Rate"):
            rag = evaluate_rag(m.staffing_rate, thresholds["Staffing Rate"])
        result.append((ws, m, rag))
    return result


def _rag_for_metric(
    metric_name: str, value: float, thresholds: dict[str, KpiThreshold]
) -> RAG:
    t = thresholds.get(metric_name)
    if not t:
        return "Green"
    return evaluate_rag(value, t)


def _status_display(rag: RAG) -> str:
    """User-facing status label (spec §2.1); internal RAG remains Green/Yellow/Red."""
    return {
        "Green": "On target",
        "Yellow": "Monitor",
        "Red": "Action needed",
    }[rag]


# Weekly RW budget / system RW% denominator (§1.3): sum of configured RW unit-days must stay 56.
RW_SYSTEM_WEEKLY_DENOMINATOR = 56


def _assert_rw_config_rw_cap_56(bases: list[BaseConfig]) -> None:
    """Fail export if base_config drifts from the 56-shift RW system denominator."""
    total = sum(int(b.rw_total_unit_days) for b in bases)
    if total != RW_SYSTEM_WEEKLY_DENOMINATOR:
        raise ValueError(
            f"base_config: sum(rw_total_unit_days) must equal {RW_SYSTEM_WEEKLY_DENOMINATOR} "
            f"(weekly RW budget / system RW% denominator); got {total}. "
            "Correct base_config before exporting."
        )


def _averages(metrics_list: list[WeekMetrics]) -> WeekMetrics | None:
    if not metrics_list:
        return None
    m0 = metrics_list[0]
    n = len(metrics_list)

    def _avg_int(values) -> int:
        # round() rather than floor division: truncation would understate
        # any averaged count surfaced in a report.
        return round(sum(values) / n)

    return WeekMetrics(
        week_start="Avg",
        required_day=m0.required_day,
        required_night=m0.required_night,
        required_total=m0.required_total,
        filled_day=_avg_int(m.filled_day for m in metrics_list),
        filled_night=_avg_int(m.filled_night for m in metrics_list),
        filled_total=_avg_int(m.filled_total for m in metrics_list),
        vacancies=_avg_int(m.vacancies for m in metrics_list),
        staffing_rate=sum(m.staffing_rate for m in metrics_list) / n,
        ot_shifts=_avg_int(m.ot_shifts for m in metrics_list),
        ot_dependency=sum(m.ot_dependency for m in metrics_list) / n,
        leave_total=_avg_int(m.leave_total for m in metrics_list),
        leave_exposure=sum(m.leave_exposure for m in metrics_list) / n,
        overnights_below=_avg_int(m.overnights_below for m in metrics_list),
        pilot_vacancies=_avg_int(m.pilot_vacancies for m in metrics_list),
        rw_total_unit_days=m0.rw_total_unit_days,
        gr_total_unit_days=m0.gr_total_unit_days,
        rw_staffed_unit_days=_avg_int(m.rw_staffed_unit_days for m in metrics_list),
        gr_staffed_unit_days=_avg_int(m.gr_staffed_unit_days for m in metrics_list),
        system_rw_pct=sum(m.system_rw_pct for m in metrics_list) / n,
        system_gr_pct=sum(m.system_gr_pct for m in metrics_list) / n,
        base_metrics=m0.base_metrics,
    )
