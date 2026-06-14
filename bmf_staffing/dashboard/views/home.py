"""Home dashboard view."""

from collections import defaultdict

from django.shortcuts import render
from staffing_tool.db import session_scope
from staffing_tool.metrics import (
    PeriodRollups,
    compute_period_rollups,
    compute_week_metrics,
)
from staffing_tool.models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyStaffing,
)
from staffing_tool.rag import evaluate_rag

from .helpers import DB_PATH, _ensure_db, _last_sunday

# Home overview cards: (card label, KpiThreshold metric_name) — values are rolling averages
HOME_OVERVIEW_METRICS = [
    ("Avg staffing rate", "Staffing Rate", "pooled_staffing_rate"),
    ("Avg OT dependency", "OT Dependency", "pooled_ot_dependency"),
    ("Avg shift exception %", "Shift Exception %", "pooled_leave_exposure"),
    ("Avg system RW coverage", "System RW Coverage %", "pooled_system_rw_pct"),
    ("Avg system GR coverage", "System GR Coverage %", "pooled_system_gr_pct"),
]


def _metric_avg_and_pooled(
    rollups: PeriodRollups, internal: str, pooled_attr: str
) -> tuple[float, float]:
    avg_map = {
        "Staffing Rate": rollups.avg_staffing_rate,
        "OT Dependency": rollups.avg_ot_dependency,
        "Shift Exception %": rollups.avg_leave_exposure,
        "System RW Coverage %": rollups.avg_system_rw_pct,
        "System GR Coverage %": rollups.avg_system_gr_pct,
    }
    pooled_map = {
        "pooled_staffing_rate": rollups.pooled_staffing_rate,
        "pooled_ot_dependency": rollups.pooled_ot_dependency,
        "pooled_leave_exposure": rollups.pooled_leave_exposure,
        "pooled_system_rw_pct": rollups.pooled_system_rw_pct,
        "pooled_system_gr_pct": rollups.pooled_system_gr_pct,
    }
    return avg_map[internal], pooled_map[pooled_attr]


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
        rollups = compute_period_rollups(metrics_list)
        kpis = []
        red_n = yellow_n = 0
        for label, internal, pooled_attr in HOME_OVERVIEW_METRICS:
            if rollups is None:
                continue
            avg_val, pooled_val = _metric_avg_and_pooled(rollups, internal, pooled_attr)
            th = thresholds.get(internal)
            if th:
                rag = evaluate_rag(pooled_val, th)
                if rag == "Red":
                    red_n += 1
                elif rag == "Yellow":
                    yellow_n += 1
            else:
                rag = "—"
            kpis.append(
                {
                    "label": label,
                    "value_pct": round(avg_val * 100, 1),
                    "pooled_pct": round(pooled_val * 100, 1),
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
                "latest_staffing_over_required": m_latest.filled_total
                > m_latest.required_total,
                "latest_staffing_rate_pct": round(m_latest.staffing_rate * 100, 1),
                "recent_weeks": recent_weeks,
                "overview_weeks_count": n_weeks,
                "overview_range_label": range_label,
            }
        )

    return render(request, "dashboard/home.html", context)
