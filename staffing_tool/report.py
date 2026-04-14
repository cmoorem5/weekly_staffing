"""
Weekly staffing summary Excel export: Board_Summary, Weekly_Detail, Trend_12_Weeks, Data_Dump.
Uses openpyxl with RAG conditional formatting and narrative generation.
"""

import os
from datetime import datetime, timedelta

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from .db import session_scope
from .leave_grid import (
    EXCEPTION_COL_KEYS,
    EXCEPTION_GRID_COLS,
    EXCEPTION_GRID_ROLES,
)
from .metrics import (
    SYSTEM_GR_MAX_SHIFTS_PER_WEEK,
    TOTAL_PERSON_SHIFTS,
    WeekMetrics,
    compute_week_metrics,
    get_metric_value,
)
from .models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyStaffing,
)
from .rag import RAG, direction_for_metric, evaluate_rag

# ----- Boston MedFlight brand (Clinical Operations guidelines) -----
# Colors: Blue #2a4492, Navy #052c47, Gray #e6e6e6, Medium Gray #cbc7d1, White #ffffff, Black #000000, Red #c12126
# Typography: Barlow; if unavailable Excel falls back (often Calibri) — Arial/Helvetica acceptable per brand.
BMF_BLUE = "2A4492"
BMF_NAVY = "052C47"
BMF_GRAY = "E6E6E6"
BMF_MEDIUM_GRAY = "CBC7D1"
BMF_WHITE = "FFFFFF"
BMF_BLACK = "000000"
BMF_RED = "C12126"

FONT_NAME = "Barlow"

FILL_GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
FILL_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
# RAG / emphasis red uses brand red (softer Excel default kept as alias only if needed)
FILL_RAG_RED = PatternFill(start_color=BMF_RED, end_color=BMF_RED, fill_type="solid")
FILL_RED = FILL_RAG_RED

FILL_BMF_NAVY = PatternFill(start_color=BMF_NAVY, end_color=BMF_NAVY, fill_type="solid")
FILL_BMF_BLUE = PatternFill(start_color=BMF_BLUE, end_color=BMF_BLUE, fill_type="solid")
FILL_BMF_GRAY_BG = PatternFill(
    start_color=BMF_GRAY, end_color=BMF_GRAY, fill_type="solid"
)
# Table column headers: BMF Gray (guidelines); section bars use Navy or Blue
FILL_HEADER_LIGHT = FILL_BMF_GRAY_BG

FILL_HEADER_DARK = FILL_BMF_NAVY

FONT_BMF_TITLE = Font(name=FONT_NAME, size=18, bold=True, color=BMF_WHITE)
FONT_BMF_SUBTITLE = Font(name=FONT_NAME, size=11, bold=True, color=BMF_NAVY)
FONT_BMF_SECTION = Font(name=FONT_NAME, size=11, bold=True, color=BMF_WHITE)
FONT_BMF_BODY = Font(name=FONT_NAME, size=11, color=BMF_BLACK)
FONT_BMF_BODY_BOLD = Font(name=FONT_NAME, size=11, bold=True, color=BMF_BLACK)
FONT_BMF_BODY_RED_BOLD = Font(name=FONT_NAME, size=11, bold=True, color=BMF_RED)
# RAG-filled cells: strong contrast (percent values)
FONT_BMF_RAG_VALUE = Font(name=FONT_NAME, size=11, bold=True, color=BMF_BLACK)
FONT_BMF_RAG_VALUE_ON_RED = Font(name=FONT_NAME, size=11, bold=True, color=BMF_WHITE)

BOLD = Font(name=FONT_NAME, bold=True, color=BMF_BLACK)
WHITE_BOLD = Font(name=FONT_NAME, bold=True, color=BMF_WHITE)
THIN_BORDER = Border(
    left=Side(style="thin", color=BMF_MEDIUM_GRAY),
    right=Side(style="thin", color=BMF_MEDIUM_GRAY),
    top=Side(style="thin", color=BMF_MEDIUM_GRAY),
    bottom=Side(style="thin", color=BMF_MEDIUM_GRAY),
)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _report_template_path() -> str:
    """Optional shell workbook: output/Weekly_staffing _report template.xlsx (project root)."""
    return os.path.join(
        _project_root(), "output", "Weekly_staffing _report template.xlsx"
    )


def _resolve_logo_path() -> str | None:
    """
    Boston MedFlight coastal logo PNG for Excel (optional).
    Set WEEKLY_STAFFING_LOGO to a file path, or place assets/bmf_coastal_logo.png in the project root.
    """
    env = os.environ.get("WEEKLY_STAFFING_LOGO", "").strip()
    if env and os.path.isfile(env):
        return env
    root = _project_root()
    candidates = [
        os.path.join(root, "assets", "bmf_coastal_logo.png"),
        os.path.join(
            root,
            "bmf_staffing",
            "dashboard",
            "static",
            "dashboard",
            "images",
            "BMF_Coastal_Logos.png",
        ),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _add_logo(ws, anchor: str = "A1", max_height_px: int = 88) -> bool:
    """Embed logo image if Pillow + file exist; returns True when an image was added."""
    path = _resolve_logo_path()
    if not path:
        return False
    try:
        from openpyxl.drawing.image import Image as XLImage
    except ImportError:
        return False
    try:
        img = XLImage(path)
        if img.height and img.height > max_height_px:
            scale = max_height_px / float(img.height)
            img.width = int(img.width * scale)
            img.height = max_height_px
        ws.add_image(img, anchor)
        return True
    except Exception:
        return False


def _new_workbook_from_template_or_empty() -> Workbook:
    """
    If the template file exists, load it and remove all sheets (use as branded shell / theme).
    Otherwise a fresh Workbook. Caller adds Board_Summary, Weekly_Detail, etc.
    """
    path = os.environ.get("WEEKLY_STAFFING_REPORT_TEMPLATE", _report_template_path())
    if os.path.isfile(path):
        try:
            wb = load_workbook(path)
            for name in list(wb.sheetnames):
                wb.remove(wb[name])
            return wb
        except Exception:
            pass
    wb = Workbook()
    if "Sheet" in wb.sheetnames:
        del wb["Sheet"]
    return wb


ALIGN_CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
ALIGN_RIGHT = Alignment(horizontal="right", vertical="center")


def _bmf_merge_band(
    ws,
    row: int,
    col_start: int,
    col_end: int,
    text: str,
    *,
    fill: PatternFill,
    font: Font,
    alignment: Alignment | None = None,
) -> None:
    """Merge a row band and apply BMF header styling."""
    top_left = ws.cell(row, col_start, text)
    if col_end > col_start:
        ws.merge_cells(
            start_row=row, start_column=col_start, end_row=row, end_column=col_end
        )
    top_left.fill = fill
    top_left.font = font
    top_left.alignment = alignment or ALIGN_CENTER


def _bmf_cell_border(ws, row: int, col: int, value, font=None, fill=None, align=None):
    c = ws.cell(row, col, value)
    c.font = font or FONT_BMF_BODY
    c.border = THIN_BORDER
    if fill:
        c.fill = fill
    if align:
        c.alignment = align
    else:
        c.alignment = ALIGN_LEFT
    return c


def _bmf_border_block(ws, r1: int, r2: int, c1: int, c2: int) -> None:
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(r, c).border = THIN_BORDER


# Weekly Staffing Detail: fixed base order (matches target output structure)
DETAIL_BASE_ORDER = ["Bedford", "Lawrence", "Mansfield", "Manchester", "Plymouth"]

# Backward-compatible names (canonical definitions in leave_grid)
LEAVE_TYPE_COLS = EXCEPTION_GRID_COLS
LEAVE_TYPE_ROWS = EXCEPTION_GRID_COLS
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


def _verify_weekly_detail_checks(
    this_metrics: WeekMetrics, row_data: WeeklyStaffing
) -> list[tuple[str, bool, str]]:
    """Run verification rules for Weekly Staffing Detail. Returns list of (check_name, passed, message)."""
    checks = []
    # Required Total = Required Day + Required Night
    rt = this_metrics.required_day + this_metrics.required_night
    ok = rt == this_metrics.required_total
    checks.append(
        ("Required Total = Day + Night", ok, f"{rt} vs {this_metrics.required_total}")
    )
    # Filled Total = Filled Day + Filled Night
    ft = this_metrics.filled_day + this_metrics.filled_night
    ok = ft == this_metrics.filled_total
    checks.append(
        ("Filled Total = Day + Night", ok, f"{ft} vs {this_metrics.filled_total}")
    )
    # Vacancies = Required Total - Filled Total
    vac = max(0, this_metrics.required_total - this_metrics.filled_total)
    ok = vac == this_metrics.vacancies
    checks.append(
        ("Vacancies = Required - Filled", ok, f"{vac} vs {this_metrics.vacancies}")
    )
    # OT Total Day = RN + MEDIC + EMT (we use all OT in Day)
    ot_rn = getattr(row_data, "ot_rn", 0) or 0
    ot_medic = getattr(row_data, "ot_medic", 0) or 0
    ot_emt = getattr(row_data, "ot_emt", 0) or 0
    total_ot = ot_rn + ot_medic + ot_emt
    ok = total_ot == this_metrics.ot_shifts
    checks.append(
        ("OT Total = RN + MEDIC + EMT", ok, f"{total_ot} vs {this_metrics.ot_shifts}")
    )
    return checks


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
    thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
    result = []
    for ws in week_starts:
        data = _load_week_with_coverage(session, ws)
        if not data:
            continue
        row, coverages, bases = data
        m = compute_week_metrics(row, coverages, bases)
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


def _generate_narrative(
    this_week: WeekMetrics,
    prior_week: WeekMetrics | None,
    avg_4w: WeekMetrics | None,
    thresholds: dict[str, KpiThreshold],
    rag_statuses: dict[str, RAG],
) -> dict[str, list[str]]:
    """Produce Key Takeaways, Drivers, Risks, Actions placeholders."""
    takeaways = []
    drivers = []
    risks = []
    actions = []

    # Overall status
    overall = rag_statuses.get("Staffing Rate", "Green")
    if overall == "Green":
        takeaways.append("Overall staffing rate is within target (Green).")
    elif overall == "Yellow":
        takeaways.append(
            "Overall staffing rate is below target (Yellow); monitor closely."
        )
    else:
        takeaways.append(
            "Overall staffing rate is below acceptable level (Red); action required."
        )

    # Week-over-week
    if prior_week:
        sr_now, sr_prior = this_week.staffing_rate, prior_week.staffing_rate
        if sr_now > sr_prior:
            takeaways.append(
                f"Staffing rate improved week-over-week ({sr_prior:.1%} → {sr_now:.1%})."
            )
        elif sr_now < sr_prior:
            takeaways.append(
                f"Staffing rate declined week-over-week ({sr_prior:.1%} → {sr_now:.1%})."
            )
        ot_now, ot_prior = this_week.ot_dependency, prior_week.ot_dependency
        if ot_now > 0.12 and ot_now > ot_prior:
            drivers.append(
                f"OT dependency increased ({ot_prior:.1%} → {ot_now:.1%}); overtime filling gaps."
            )
        if this_week.leave_exposure > 0.25:
            drivers.append(
                f"Leave exposure at {this_week.leave_exposure:.1%}; contributes to coverage pressure."
            )
        if this_week.ot_dependency > 0.12:
            risks.append("High OT dependency; fatigue and sustainability risk.")
        if (
            rag_statuses.get("System RW Coverage %") == "Red"
            or rag_statuses.get("System GR Coverage %") == "Red"
        ):
            risks.append(
                "RW or GR system coverage below threshold; readiness and capacity risk."
            )
    else:
        takeaways.append("No prior week for comparison.")

    # Actions (placeholders + suggestions from flags)
    if rag_statuses.get("Staffing Rate") == "Red":
        actions.append("Address staffing shortfall (scheduling/recruiting).")
    if rag_statuses.get("OT Dependency") == "Red":
        actions.append("Reduce OT dependency; review scheduling and capacity.")
    if not actions:
        actions.append("Maintain current staffing and leave monitoring.")
    actions.append("[User-editable action item]")
    actions.append("[User-editable action item]")

    return {
        "key_takeaways": takeaways[:4],
        "drivers": drivers[:4],
        "risks": risks[:3],
        "actions": actions,
    }


def _write_board_summary(
    wb: Workbook,
    week_start: str,
    week_end: str,
    this_metrics: WeekMetrics,
    prior_metrics: WeekMetrics | None,
    avg_4w: WeekMetrics | None,
    avg_12w: WeekMetrics | None,
    trend_list: list[tuple[str, WeekMetrics]],
    thresholds: dict[str, KpiThreshold],
    narrative: dict[str, list[str]],
) -> None:
    ws = wb.create_sheet("Board_Summary", 0)
    row = 1

    logo_ok = _add_logo(ws, "A1", max_height_px=76)
    if logo_ok:
        ws.row_dimensions[1].height = 78
        ws.merge_cells(start_row=1, start_column=2, end_row=1, end_column=7)
        t = ws.cell(row, 2, "Weekly staffing summary")
        t.font = Font(name=FONT_NAME, bold=True, size=14, color=BMF_BLACK)
        t.alignment = ALIGN_LEFT
        row += 1
        ws.merge_cells(start_row=2, start_column=2, end_row=2, end_column=7)
        p = ws.cell(row, 2, f"Period: {week_start} to {week_end}")
        p.font = FONT_BMF_BODY
        p.alignment = ALIGN_LEFT
        row += 2
    else:
        ws.cell(row, 1, "Weekly staffing summary").font = Font(
            name=FONT_NAME, bold=True, size=14, color=BMF_BLACK
        )
        row += 1
        ws.cell(row, 1, f"Period: {week_start} to {week_end}").font = FONT_BMF_BODY
        row += 2

    # KPI Panel: (display_label, internal_metric_name)
    board_metrics = [
        ("Staffing Rate", "Staffing Rate"),
        ("Backfill Rate", "OT Dependency"),
        ("Shift Exception %", "Leave Exposure"),
        ("System RW Coverage %", "System RW Coverage %"),
        ("System GR Coverage %", "System GR Coverage %"),
    ]
    headers = [
        "Metric",
        "This Week",
        "Prior Week",
        "4-Week Avg",
        "12-Week Avg",
        "Status",
        "Direction",
    ]
    kpi_header_row = row
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row, c, h)
        cell.font = BOLD
        cell.border = THIN_BORDER
    row += 1

    for display_name, metric_key in board_metrics:
        val_this = get_metric_value(this_metrics, metric_key)
        val_prior = (
            get_metric_value(prior_metrics, metric_key) if prior_metrics else None
        )
        val_4 = get_metric_value(avg_4w, metric_key) if avg_4w else None
        val_12 = get_metric_value(avg_12w, metric_key) if avg_12w else None
        rag = (
            _rag_for_metric(metric_key, val_this or 0, thresholds)
            if val_this is not None
            else "Green"
        )
        direction = direction_for_metric(metric_key, val_this or 0, val_prior)
        ws.cell(row, 1, display_name).border = THIN_BORDER
        _write_pct_or_num(ws, row, 2, val_this, metric_key)
        _write_pct_or_num(ws, row, 3, val_prior, metric_key)
        _write_pct_or_num(ws, row, 4, val_4, metric_key)
        _write_pct_or_num(ws, row, 5, val_12, metric_key)
        status_cell = ws.cell(row, 6, rag)
        status_cell.border = THIN_BORDER
        if rag == "Green":
            status_cell.fill = FILL_GREEN
        elif rag == "Yellow":
            status_cell.fill = FILL_YELLOW
        else:
            status_cell.fill = FILL_RED
        ws.cell(row, 7, direction).border = THIN_BORDER
        row += 1

    row += 1
    ws.cell(row, 1, "Narrative").font = BOLD
    row += 1
    ws.cell(row, 1, "Key Takeaways").font = BOLD
    row += 1
    for bullet in narrative["key_takeaways"]:
        ws.cell(row, 1, "• " + bullet)
        row += 1
    row += 1
    ws.cell(row, 1, "Drivers").font = BOLD
    row += 1
    for bullet in narrative["drivers"]:
        ws.cell(row, 1, "• " + bullet)
        row += 1
    row += 1
    ws.cell(row, 1, "Risks (fatigue/coverage/readiness)").font = BOLD
    row += 1
    for bullet in narrative["risks"]:
        ws.cell(row, 1, "• " + bullet)
        row += 1
    row += 1
    ws.cell(row, 1, "Actions / Decisions Needed").font = BOLD
    row += 1
    for bullet in narrative["actions"]:
        ws.cell(row, 1, "• " + bullet)
        row += 1

    row += 2
    ws.cell(row, 1, "Base Coverage").font = BOLD
    row += 1
    ws.cell(row, 1, "Base").font = BOLD
    ws.cell(row, 2, "RW %").font = BOLD
    ws.cell(row, 3, "GR %").font = BOLD
    ws.cell(row, 4, "Notes").font = BOLD
    row += 1
    if this_metrics.base_metrics:
        t_rw = thresholds.get("System RW Coverage %")
        t_gr = thresholds.get("System GR Coverage %")
        for base_name in sorted(this_metrics.base_metrics.keys()):
            pcts = this_metrics.base_metrics[base_name]
            rw_pct, gr_pct = pcts.get("rw_pct", 0), pcts.get("gr_pct", 0)
            rw_rag = evaluate_rag(rw_pct, t_rw) if t_rw else "Green"
            gr_rag = evaluate_rag(gr_pct, t_gr) if t_gr else "Green"
            notes = ""
            if rw_rag != "Green" or gr_rag != "Green":
                notes = (
                    "Below threshold"
                    if (rw_rag == "Red" or gr_rag == "Red")
                    else "Monitor"
                )
            ws.cell(row, 1, base_name)
            _write_pct_cell(ws, row, 2, rw_pct, rw_rag)
            _write_pct_cell(ws, row, 3, gr_pct, gr_rag)
            ws.cell(row, 4, notes)
            row += 1

    # Column widths and freeze (keep KPI header row visible when scrolling)
    ws.column_dimensions["A"].width = 28
    for c in range(2, 8):
        ws.column_dimensions[get_column_letter(c)].width = 14
    ws.freeze_panes = f"A{kpi_header_row + 1}"


def _write_pct_or_num(
    ws, row: int, col: int, value: float | None, metric_name: str
) -> None:
    if value is None:
        ws.cell(row, col, "").border = THIN_BORDER
        return
    cell = ws.cell(row, col)
    cell.border = THIN_BORDER
    percent_metrics = {
        "Staffing Rate",
        "OT Dependency",
        "Backfill Rate",
        "Leave Exposure",
        "Shift Exception %",
        "System RW Coverage %",
        "System GR Coverage %",
    }
    if metric_name in percent_metrics:
        cell.value = value
        cell.number_format = "0.0%"
    else:
        cell.value = value if isinstance(value, (int, float)) else value
        if isinstance(value, float) and value == int(value):
            cell.value = int(value)


def _write_pct_cell(ws, row: int, col: int, value: float, rag: RAG) -> None:
    cell = ws.cell(row, col)
    cell.value = value
    cell.number_format = "0.0%"
    cell.border = THIN_BORDER
    cell.alignment = ALIGN_CENTER
    if rag == "Green":
        cell.fill = FILL_GREEN
        cell.font = FONT_BMF_RAG_VALUE
    elif rag == "Yellow":
        cell.fill = FILL_YELLOW
        cell.font = FONT_BMF_RAG_VALUE
    else:
        cell.fill = FILL_RED
        cell.font = FONT_BMF_RAG_VALUE_ON_RED


def _write_weekly_detail(
    wb: Workbook,
    week_start: str,
    week_end: str,
    row_data: WeeklyStaffing,
    this_metrics: WeekMetrics,
    base_configs: list[BaseConfig],
    leave_breakdown: dict | None = None,
    thresholds: dict[str, KpiThreshold] | None = None,
) -> None:
    """Write Weekly_Detail sheet: BMF-branded layout; executive summary, OT, exceptions grid, base coverage."""
    ws = wb.create_sheet("Weekly_Detail", 1)
    base_by_name = {b.base_name: b for b in base_configs}
    base_metrics = this_metrics.base_metrics or {}
    breakdown = leave_breakdown or {}
    grid_leave_total, col_totals_grid = (
        _leave_totals_from_breakdown(breakdown) if breakdown else (0, [0] * 6)
    )
    use_grid_leave = bool(breakdown)
    display_leave_total = (
        grid_leave_total if use_grid_leave else this_metrics.leave_total
    )
    display_leave_exp = (
        (grid_leave_total / TOTAL_PERSON_SHIFTS)
        if use_grid_leave
        else this_metrics.leave_exposure
    )

    # ---- Brand header (optional coastal logo in column A) ----
    r = 1
    detail_logo = _add_logo(ws, "A1", max_height_px=64)
    if detail_logo:
        ws.row_dimensions[1].height = 62
        ws.cell(1, 1).fill = FILL_BMF_NAVY
        _bmf_merge_band(
            ws,
            r,
            2,
            7,
            "Boston MedFlight — Weekly Staffing Detail",
            fill=FILL_BMF_NAVY,
            font=FONT_BMF_TITLE,
        )
    else:
        _bmf_merge_band(
            ws,
            r,
            1,
            7,
            "Boston MedFlight — Weekly Staffing Detail",
            fill=FILL_BMF_NAVY,
            font=FONT_BMF_TITLE,
        )
    r = 2
    period_line = f"Reporting period: Sunday {week_start} through Saturday {week_end}"
    if detail_logo:
        _bmf_merge_band(
            ws,
            r,
            2,
            7,
            period_line,
            fill=FILL_BMF_GRAY_BG,
            font=FONT_BMF_SUBTITLE,
            alignment=ALIGN_CENTER,
        )
        ws.cell(2, 1).fill = FILL_BMF_GRAY_BG
    else:
        _bmf_merge_band(
            ws,
            r,
            1,
            7,
            period_line,
            fill=FILL_BMF_GRAY_BG,
            font=FONT_BMF_SUBTITLE,
            alignment=ALIGN_CENTER,
        )
    r = 3
    _bmf_merge_band(
        ws, r, 1, 7, "Executive summary", fill=FILL_BMF_NAVY, font=FONT_BMF_SECTION
    )
    r = 4
    medic_u = getattr(row_data, "medic_unpartnered", 0) or 0
    rn_u = getattr(row_data, "rn_unpartnered_staff", 0) or 0
    exec_row_start = r
    _bmf_cell_border(ws, r, 1, "Required (Day)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, this_metrics.required_day)
    _bmf_cell_border(ws, r, 4, "Filled (Day)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 5, this_metrics.filled_day)
    r += 1
    _bmf_cell_border(ws, r, 1, "Required (Night)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, this_metrics.required_night)
    _bmf_cell_border(ws, r, 4, "Filled (Night)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 5, this_metrics.filled_night)
    r += 1
    _bmf_cell_border(ws, r, 1, "Required (total)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, this_metrics.required_total)
    _bmf_cell_border(ws, r, 4, "Filled (total)", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 5, this_metrics.filled_total)
    r += 1
    _bmf_cell_border(ws, r, 1, "Vacancies", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, this_metrics.vacancies)
    _bmf_cell_border(ws, r, 4, "Shift exceptions (total)", FONT_BMF_BODY_BOLD)
    c_exc_tot = _bmf_cell_border(ws, r, 5, display_leave_total)
    c_exc_tot.font = FONT_BMF_BODY_RED_BOLD
    r += 1
    _bmf_cell_border(ws, r, 1, "Medic unpartnered", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, medic_u)
    _bmf_cell_border(ws, r, 4, "RN unpartnered staff", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 5, rn_u)
    r += 1
    _bmf_cell_border(ws, r, 1, "Shift exception %", FONT_BMF_BODY_BOLD)
    c_pct = _bmf_cell_border(ws, r, 2, display_leave_exp)
    c_pct.number_format = "0.0%"
    c_pct.font = FONT_BMF_BODY_RED_BOLD
    _bmf_cell_border(ws, r, 4, "Staffing rate", FONT_BMF_BODY_BOLD)
    sr_rag = (
        _rag_for_metric("Staffing Rate", this_metrics.staffing_rate, thresholds)
        if thresholds
        else "Green"
    )
    _write_pct_cell(ws, r, 5, this_metrics.staffing_rate, sr_rag)
    _bmf_border_block(ws, exec_row_start, r, 1, 5)
    for exec_r in range(exec_row_start, r + 1):
        for exec_c in (2, 5):
            ws.cell(exec_r, exec_c).alignment = ALIGN_CENTER

    # ---- Overtime ----
    # Use only explicit day/night columns — do not map legacy ot_rn / ot_medic / ot_emt
    # into "Day" when splits are zero (that falsely showed total RN OT as day OT).
    ot_rn_day = getattr(row_data, "ot_rn_day", 0) or 0
    ot_rn_night = getattr(row_data, "ot_rn_night", 0) or 0
    ot_medic_day = getattr(row_data, "ot_medic_day", 0) or 0
    ot_medic_night = getattr(row_data, "ot_medic_night", 0) or 0
    ot_emt_day = getattr(row_data, "ot_emt_day", 0) or 0
    ot_emt_night = getattr(row_data, "ot_emt_night", 0) or 0
    total_ot_day = ot_rn_day + ot_medic_day + ot_emt_day
    total_ot_night = ot_rn_night + ot_medic_night + ot_emt_night
    # Backfill matches Board_Summary / compute_week_metrics (includes legacy totals when splits are unset).
    ot_dep = this_metrics.ot_dependency

    r += 1
    _bmf_merge_band(
        ws,
        r,
        1,
        7,
        "Overtime (shift counts only)",
        fill=FILL_BMF_NAVY,
        font=FONT_BMF_SECTION,
    )
    r += 1
    ot_table_start = r
    _bmf_cell_border(ws, r, 1, "Role", FONT_BMF_BODY_BOLD, fill=FILL_HEADER_LIGHT)
    _bmf_cell_border(
        ws, r, 2, "Day", FONT_BMF_BODY_BOLD, fill=FILL_HEADER_LIGHT, align=ALIGN_CENTER
    )
    _bmf_cell_border(
        ws,
        r,
        4,
        "Night",
        FONT_BMF_BODY_BOLD,
        fill=FILL_HEADER_LIGHT,
        align=ALIGN_CENTER,
    )
    _bmf_border_block(ws, r, r, 1, 4)
    r += 1
    _bmf_cell_border(ws, r, 1, "RN", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, ot_rn_day, align=ALIGN_CENTER)
    _bmf_cell_border(ws, r, 4, ot_rn_night, align=ALIGN_CENTER)
    r += 1
    _bmf_cell_border(ws, r, 1, "Medic", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, ot_medic_day, align=ALIGN_CENTER)
    _bmf_cell_border(ws, r, 4, ot_medic_night, align=ALIGN_CENTER)
    r += 1
    _bmf_cell_border(ws, r, 1, "EMT", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, ot_emt_day, align=ALIGN_CENTER)
    _bmf_cell_border(ws, r, 4, ot_emt_night, align=ALIGN_CENTER)
    r += 1
    _bmf_cell_border(ws, r, 1, "Total", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, r, 2, total_ot_day, align=ALIGN_CENTER)
    _bmf_cell_border(ws, r, 4, total_ot_night, align=ALIGN_CENTER)
    _bmf_border_block(ws, ot_table_start, r, 1, 4)
    r += 1
    _bmf_cell_border(ws, r, 1, "Backfill rate (OT / filled total)", FONT_BMF_BODY_BOLD)
    c_ot = _bmf_cell_border(ws, r, 2, ot_dep, align=ALIGN_CENTER)
    c_ot.number_format = "0.0%"
    c_ot.font = FONT_BMF_BODY_RED_BOLD
    _bmf_cell_border(ws, r, 3, "")
    _bmf_cell_border(ws, r, 4, "")
    _bmf_cell_border(ws, r, 5, "")
    _bmf_border_block(ws, r, r, 1, 5)

    # ---- Schedule exceptions ----
    leave_types = LEAVE_TYPE_COLS
    roles = EXCEPTION_ROLES

    def _exc_count(role: str, keys: list[str]) -> int:
        return _exc_count_breakdown(breakdown, role, keys)

    if breakdown:
        col_totals = col_totals_grid
    else:
        col_totals = [
            row_data.leave_at or 0,
            row_data.leave_lt or 0,
            row_data.leave_sick or 0,
            row_data.leave_loa or 0,
            getattr(row_data, "leave_jury", 0) or 0,
            getattr(row_data, "leave_brev", 0) or 0,
        ]

    r += 1
    _bmf_merge_band(
        ws,
        r,
        1,
        7,
        "Schedule exceptions by role and type",
        fill=FILL_BMF_NAVY,
        font=FONT_BMF_SECTION,
    )
    r += 1
    _bmf_cell_border(ws, r, 1, "Role", FONT_BMF_BODY_BOLD, fill=FILL_HEADER_LIGHT)
    for c, lt in enumerate(leave_types, start=2):
        cell = ws.cell(r, c, lt)
        cell.font = FONT_BMF_BODY_BOLD
        cell.fill = FILL_HEADER_LIGHT
        cell.border = THIN_BORDER
        cell.alignment = ALIGN_CENTER
    row_num = r + 1
    for role in roles:
        _bmf_cell_border(ws, row_num, 1, role, FONT_BMF_BODY_BOLD)
        _bmf_cell_border(
            ws, row_num, 2, _exc_count(role, ["AT"]), align=ALIGN_CENTER
        )
        _bmf_cell_border(
            ws,
            row_num,
            3,
            _exc_count(role, ["LT-D", "LT-N", "LT"]),
            align=ALIGN_CENTER,
        )
        _bmf_cell_border(ws, row_num, 4, _exc_count(role, ["SICK"]), align=ALIGN_CENTER)
        _bmf_cell_border(
            ws, row_num, 5, _exc_count(role, ["LOA", "PFML"]), align=ALIGN_CENTER
        )
        _bmf_cell_border(ws, row_num, 6, _exc_count(role, ["JURY"]), align=ALIGN_CENTER)
        _bmf_cell_border(ws, row_num, 7, _exc_count(role, ["BREV"]), align=ALIGN_CENTER)
        _bmf_border_block(ws, row_num, row_num, 1, 7)
        row_num += 1
    _bmf_cell_border(ws, row_num, 1, "Total", FONT_BMF_BODY_BOLD)
    for ci, total in enumerate(col_totals, start=2):
        _bmf_cell_border(ws, row_num, ci, total, align=ALIGN_CENTER)
    _bmf_border_block(ws, row_num, row_num, 1, 7)
    row_num += 1

    # ---- Base coverage ----
    _bmf_merge_band(
        ws,
        row_num,
        1,
        7,
        "Base coverage (RW / GR)",
        fill=FILL_BMF_NAVY,
        font=FONT_BMF_SECTION,
    )
    row_num += 1
    for c, h in enumerate(
        ["Base", "RW/D", "RW/N", "GR/D", "GR/N", "RW %", "GR %"], start=1
    ):
        cell = ws.cell(row_num, c, h)
        cell.font = FONT_BMF_BODY_BOLD
        cell.fill = FILL_HEADER_LIGHT
        cell.border = THIN_BORDER
        cell.alignment = ALIGN_CENTER
    row_num += 1
    t_rw = thresholds.get("System RW Coverage %") if thresholds else None
    t_gr = thresholds.get("System GR Coverage %") if thresholds else None
    sys_rw_d, sys_rw_n, sys_gr_d, sys_gr_n = 0, 0, 0, 0
    rw_totals_by_base = {}
    gr_totals_by_base = {}
    for cfg in base_configs:
        rw_totals_by_base[cfg.base_name] = cfg.rw_total_unit_days
        gr_totals_by_base[cfg.base_name] = cfg.gr_total_unit_days
    for base_name in DETAIL_BASE_ORDER:
        cfg = base_by_name.get(base_name)
        pct = base_metrics.get(base_name, {})
        rw_staffed = pct.get("rw_staffed", 0)
        gr_staffed = pct.get("gr_staffed", 0)
        rw_total = cfg.rw_total_unit_days if cfg else 0
        gr_total = cfg.gr_total_unit_days if cfg else 0
        rw_d = int(pct.get("rw_d", rw_staffed))
        rw_n = int(pct.get("rw_n", 0))
        gr_d = int(pct.get("gr_d", gr_staffed))
        gr_n = int(pct.get("gr_n", 0))
        sys_rw_d += rw_d
        sys_rw_n += rw_n
        sys_gr_d += gr_d
        sys_gr_n += gr_n
        _bmf_cell_border(ws, row_num, 1, base_name)
        _bmf_cell_border(
            ws, row_num, 2, "" if not rw_total else rw_d, align=ALIGN_CENTER
        )
        _bmf_cell_border(
            ws, row_num, 3, "" if not rw_total else rw_n, align=ALIGN_CENTER
        )
        _bmf_cell_border(
            ws, row_num, 4, "" if not gr_total else gr_d, align=ALIGN_CENTER
        )
        _bmf_cell_border(
            ws, row_num, 5, "" if not gr_total else gr_n, align=ALIGN_CENTER
        )
        if rw_total:
            rw_pct_val = rw_staffed / rw_total
            rw_rag = evaluate_rag(rw_pct_val, t_rw) if t_rw else "Green"
            _write_pct_cell(ws, row_num, 6, rw_pct_val, rw_rag)
        else:
            _bmf_cell_border(ws, row_num, 6, "", align=ALIGN_CENTER)
        if gr_total:
            gr_pct_val = gr_staffed / gr_total
            gr_rag = evaluate_rag(gr_pct_val, t_gr) if t_gr else "Green"
            _write_pct_cell(ws, row_num, 7, gr_pct_val, gr_rag)
        else:
            _bmf_cell_border(ws, row_num, 7, "", align=ALIGN_CENTER)
        _bmf_border_block(ws, row_num, row_num, 1, 7)
        row_num += 1
    _bmf_cell_border(ws, row_num, 1, "System total", FONT_BMF_BODY_BOLD)
    _bmf_cell_border(ws, row_num, 2, sys_rw_d, align=ALIGN_CENTER)
    _bmf_cell_border(ws, row_num, 3, sys_rw_n, align=ALIGN_CENTER)
    _bmf_cell_border(ws, row_num, 4, sys_gr_d, align=ALIGN_CENTER)
    _bmf_cell_border(ws, row_num, 5, sys_gr_n, align=ALIGN_CENTER)
    sys_rw_total = sum(rw_totals_by_base.get(b, 0) for b in DETAIL_BASE_ORDER)
    # System % on the sheet matches dashboard/board: RW = staffed / sum RW caps; GR = staffed / 28.
    srw_rag = (
        evaluate_rag(this_metrics.system_rw_pct, t_rw)
        if t_rw and sys_rw_total
        else "Green"
    )
    sgr_rag = (
        evaluate_rag(this_metrics.system_gr_pct, t_gr)
        if t_gr and SYSTEM_GR_MAX_SHIFTS_PER_WEEK > 0
        else "Green"
    )
    _write_pct_cell(ws, row_num, 6, this_metrics.system_rw_pct, srw_rag)
    _write_pct_cell(ws, row_num, 7, this_metrics.system_gr_pct, sgr_rag)
    _bmf_border_block(ws, row_num, row_num, 1, 7)

    ws.freeze_panes = "A4"
    ws.column_dimensions["A"].width = 28
    for c in range(2, 8):
        ws.column_dimensions[get_column_letter(c)].width = 14


def _write_trend_sheet(
    wb: Workbook,
    trend_list: list[tuple[str, WeekMetrics]],
    thresholds: dict[str, KpiThreshold],
) -> None:
    ws = wb.create_sheet("Trend_12_Weeks", 2)
    headers = [
        "Week Start",
        "Staffing Rate",
        "Backfill Rate",
        "Shift Exception %",
        "System RW %",
        "System GR %",
        "Status (RAG)",
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(1, c, h).font = BOLD
    row = 2
    for week_start, m in trend_list:
        ws.cell(row, 1, week_start)
        ws.cell(row, 2, m.staffing_rate).number_format = "0.0%"
        ws.cell(row, 3, m.ot_dependency).number_format = "0.0%"
        ws.cell(row, 4, m.leave_exposure).number_format = "0.0%"
        ws.cell(row, 5, m.system_rw_pct).number_format = "0.0%"
        ws.cell(row, 6, m.system_gr_pct).number_format = "0.0%"
        rag = _rag_for_metric("Staffing Rate", m.staffing_rate, thresholds)
        ws.cell(row, 7, rag)
        row += 1
    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 12


def _write_data_dump(wb: Workbook, session: Session) -> None:
    ws = wb.create_sheet("Data_Dump", 3)
    rows = session.query(WeeklyStaffing).order_by(WeeklyStaffing.week_start).all()
    if not rows:
        return
    # Headers from first row
    attrs = [
        "week_start",
        "day_target",
        "night_min",
        "filled_day",
        "filled_night",
        "ot_shifts",
        "leave_at",
        "leave_lt",
        "leave_sick",
        "leave_loa",
        "leave_pfml",
        "medic_unpartnered",
        "rn_unpartnered_staff",
        "overnights_below",
        "pilot_vacancies",
        "notes",
        "entered_by",
        "created_at",
        "updated_at",
    ]
    for c, a in enumerate(attrs, 1):
        ws.cell(1, c, a).font = BOLD
    for r, row in enumerate(rows, 2):
        for c, a in enumerate(attrs, 1):
            val = getattr(row, a, None)
            ws.cell(r, c, val)
    ws.freeze_panes = "A2"


def _averages(metrics_list: list[WeekMetrics]) -> WeekMetrics | None:
    if not metrics_list:
        return None
    m0 = metrics_list[0]
    n = len(metrics_list)
    return WeekMetrics(
        week_start="Avg",
        required_day=m0.required_day,
        required_night=m0.required_night,
        required_total=m0.required_total,
        filled_day=sum(m.filled_day for m in metrics_list) // n,
        filled_night=sum(m.filled_night for m in metrics_list) // n,
        filled_total=sum(m.filled_total for m in metrics_list) // n,
        vacancies=sum(m.vacancies for m in metrics_list) // n,
        staffing_rate=sum(m.staffing_rate for m in metrics_list) / n,
        ot_shifts=sum(m.ot_shifts for m in metrics_list) // n,
        ot_dependency=sum(m.ot_dependency for m in metrics_list) / n,
        leave_total=sum(m.leave_total for m in metrics_list) // n,
        leave_exposure=sum(m.leave_exposure for m in metrics_list) / n,
        overnights_below=sum(m.overnights_below for m in metrics_list) // n,
        pilot_vacancies=sum(m.pilot_vacancies for m in metrics_list) // n,
        rw_total_unit_days=m0.rw_total_unit_days,
        gr_total_unit_days=m0.gr_total_unit_days,
        rw_staffed_unit_days=sum(m.rw_staffed_unit_days for m in metrics_list) // n,
        gr_staffed_unit_days=sum(m.gr_staffed_unit_days for m in metrics_list) // n,
        system_rw_pct=sum(m.system_rw_pct for m in metrics_list) / n,
        system_gr_pct=sum(m.system_gr_pct for m in metrics_list) / n,
        base_metrics=m0.base_metrics,
    )


def export_board_pack(
    db_path: str | None,
    week_start: str,
    trend_weeks: int = 12,
    output_dir: str = "output",
) -> str:
    """
    Generate Weekly_staffing_summary_<week_start>_to_<week_end>.xlsx.
    Returns path to the written file.
    """
    os.makedirs(output_dir, exist_ok=True)
    week_end = _week_end(week_start)
    filename = f"Weekly_staffing_summary_{week_start}_to_{week_end}.xlsx"
    filepath = os.path.join(output_dir, filename)

    with session_scope(db_path) as session:
        thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
        data = _load_week_with_coverage(session, week_start)
        if not data:
            raise ValueError(f"No weekly data for week_start={week_start}")

        row, coverages, bases = data
        leave_details = (
            session.query(WeeklyLeaveDetail)
            .filter(WeeklyLeaveDetail.week_start == week_start)
            .all()
        )
        leave_breakdown = {(r.role, r.leave_type): r.count for r in leave_details}
        this_metrics = compute_week_metrics(row, coverages, bases)

        week_starts = _load_weeks_ordered(
            session, trend_weeks, through_week_start=week_start
        )
        trend_list = _metrics_for_weeks(
            session, week_starts
        )  # list of (week_start, WeekMetrics, RAG)
        trend_metrics = [m for _, m, _ in trend_list]
        prior_metrics = None
        idx_this = next(
            (i for i, (ws, _, _) in enumerate(trend_list) if ws == week_start), None
        )
        if idx_this is not None and idx_this > 0:
            prior_metrics = trend_list[idx_this - 1][1]

        last_4 = trend_metrics[-4:] if len(trend_metrics) >= 4 else trend_metrics
        avg_4w = _averages(last_4) if last_4 else None
        avg_12w = _averages(trend_metrics) if trend_metrics else None

        rag_statuses = {}
        for name in [
            "Staffing Rate",
            "OT Dependency",
            "Leave Exposure",
            "System RW Coverage %",
            "System GR Coverage %",
        ]:
            v = get_metric_value(this_metrics, name)
            if v is not None:
                rag_statuses[name] = _rag_for_metric(name, v, thresholds)

        narrative = _generate_narrative(
            this_metrics, prior_metrics, avg_4w, thresholds, rag_statuses
        )

        wb = _new_workbook_from_template_or_empty()

        trend_data = [(ws, m) for (ws, m, _) in trend_list]
        _write_board_summary(
            wb,
            week_start,
            week_end,
            this_metrics,
            prior_metrics,
            avg_4w,
            avg_12w,
            trend_data,
            thresholds,
            narrative,
        )
        _write_weekly_detail(
            wb,
            week_start,
            week_end,
            row,
            this_metrics,
            bases,
            leave_breakdown=leave_breakdown,
            thresholds=thresholds,
        )
        _write_trend_sheet(wb, trend_data, thresholds)
        _write_data_dump(wb, session)

        wb.save(filepath)

        # Verification: reconciliation checks (console output)
        print("Verification (Weekly Staffing Detail):")
        for name, passed, msg in _verify_weekly_detail_checks(this_metrics, row):
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}: {msg}")
        print(f"  -> Saved: {filepath}")

    return filepath


def export_week_excel(
    db_path: str | None,
    week_start: str,
    output_dir: str = "output",
) -> str:
    """Single-week export (simplified one-sheet or same structure). Reuse board pack with 1 week trend."""
    return export_board_pack(db_path, week_start, trend_weeks=1, output_dir=output_dir)
