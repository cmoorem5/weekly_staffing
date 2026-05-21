"""
Weekly staffing summary Excel export: Board_Summary, Weekly_Detail, Trend_12_Weeks, Data_Dump.
Uses openpyxl; status fills are computed in Python (not Excel conditional formatting).
"""

import os
import subprocess
from datetime import UTC, datetime, timedelta

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session

from . import __version__ as STAFFING_TOOL_VERSION
from .db import session_scope
from .leave_grid import (
    EXCEPTION_COL_KEYS,
    EXCEPTION_GRID_COLS,
    EXCEPTION_GRID_ROLES,
)
from .metrics import (
    TOTAL_PERSON_SHIFTS,
    PeriodRollups,
    WeekMetrics,
    compute_period_rollups,
    compute_week_metrics,
    get_metric_value,
    get_pooled_metric_value,
)
from .models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyStaffing,
)
from .rag import RAG, direction_for_metric, evaluate_rag
from .validation import ot_action_ceiling, shift_exception_monitor_ceiling

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

# §2.2 severity gradient (fills)
FILL_GREEN_SOFT = PatternFill(start_color="EAF5E9", end_color="EAF5E9", fill_type="solid")
FILL_GREEN_FULL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
FILL_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
FILL_RED_SOFT = PatternFill(start_color="F4CCCC", end_color="F4CCCC", fill_type="solid")
FILL_RED_FULL = PatternFill(start_color=BMF_RED, end_color=BMF_RED, fill_type="solid")
# Back-compat aliases
FILL_GREEN = FILL_GREEN_FULL
FILL_RAG_RED = FILL_RED_FULL
FILL_RED = FILL_RED_FULL

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
# RAG-filled cells: strong contrast (percent values)
FONT_BMF_RAG_VALUE = Font(name=FONT_NAME, size=11, bold=True, color=BMF_BLACK)
FONT_BMF_RAG_VALUE_ON_RED = Font(name=FONT_NAME, size=11, bold=True, color=BMF_WHITE)

BOLD = Font(name=FONT_NAME, bold=True, color=BMF_BLACK)
WHITE_BOLD = Font(name=FONT_NAME, bold=True, color=BMF_WHITE)
FONT_NA = Font(name=FONT_NAME, size=11, italic=True, color="999999")
FONT_SUBTITLE_MUTED = Font(name=FONT_NAME, size=11, italic=True, color=BMF_MEDIUM_GRAY)
FONT_GENERATED = Font(name=FONT_NAME, size=9, italic=True, color=BMF_MEDIUM_GRAY)
FONT_FOOTER_META = Font(name=FONT_NAME, size=9, italic=True, color="666666")
THIN_BORDER = Border(
    left=Side(style="thin", color=BMF_MEDIUM_GRAY),
    right=Side(style="thin", color=BMF_MEDIUM_GRAY),
    top=Side(style="thin", color=BMF_MEDIUM_GRAY),
    bottom=Side(style="thin", color=BMF_MEDIUM_GRAY),
)

FILL_BAND_ALT = PatternFill(start_color="F7F7F7", end_color="F7F7F7", fill_type="solid")
FILL_BAND_TOTAL = PatternFill(start_color=BMF_GRAY, end_color=BMF_GRAY, fill_type="solid")
SIDE_SEP = Side(style="thin", color=BMF_MEDIUM_GRAY)

# §1.2 — which base/unit/shift cells exist (False → render "N/A")
BASE_UNIT_CELL_CONFIGURED: dict[str, dict[str, bool]] = {
    "Bedford": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": True},
    "Lawrence": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": False},
    "Manchester": {"rw_d": True, "rw_n": False, "gr_d": False, "gr_n": False},
    "Mansfield": {"rw_d": True, "rw_n": False, "gr_d": True, "gr_n": False},
    "Plymouth": {"rw_d": True, "rw_n": True, "gr_d": True, "gr_n": False},
}


def _moderate_red_soft(value: float, t: KpiThreshold) -> bool:
    """
    Soft red vs full red: 'moderate' when the value is not in the worst part of the red band
    (roughly 40–80% of the way from yellow boundary toward the worst edge → soft).
    """
    hi = (t.higher_is_better or 0) != 0
    if hi:
        # Higher is better → red on the low side
        edge = t.yellow_min
        if edge is None or edge <= 0:
            return True
        if value >= edge:
            return True
        depth = (edge - value) / edge
        return depth < 0.8
    # Lower is better → red on the high side
    edge = t.yellow_max
    if edge is None:
        return True
    if value <= edge:
        return True
    span = max((t.red_max or 1.0) - edge, 1e-9)
    depth = (value - edge) / span
    return depth < 0.8


def _kpi_notable(metric_key: str, val: float, this_metrics: WeekMetrics) -> bool:
    """§2.2: notable → full green when On target."""
    if metric_key == "System GR Coverage %":
        return val >= 0.95
    if metric_key == "System RW Coverage %":
        return val >= 0.95
    if metric_key in ("Staffing Rate", "OT Dependency", "Shift Exception %"):
        return False
    return False


def _base_rw_cell_notable(rw_pct_val: float) -> bool:
    """Per-base RW% cell: notable at 100% coverage."""
    return rw_pct_val >= 1.0 - 1e-9


def _base_gr_cell_notable(gr_pct_val: float) -> bool:
    """Per-base GR% cell: notable at ≥95% (spec) or 100%."""
    return gr_pct_val >= 0.95 - 1e-9


def _fill_and_font_for_status(
    rag: RAG,
    *,
    notable: bool,
    value: float,
    thr: KpiThreshold | None,
) -> tuple[PatternFill, Font]:
    if rag == "Green":
        fill = FILL_GREEN_FULL if notable else FILL_GREEN_SOFT
        return fill, FONT_BMF_RAG_VALUE
    if rag == "Yellow":
        return FILL_YELLOW, FONT_BMF_RAG_VALUE
    if thr is not None and _moderate_red_soft(value, thr):
        return FILL_RED_SOFT, FONT_BMF_RAG_VALUE
    return FILL_RED_FULL, FONT_BMF_RAG_VALUE_ON_RED


def _target_display(metric_key: str, t: KpiThreshold | None) -> str:
    """Green-threshold hint for Target column (§1.4 display)."""
    if not t:
        return "—"
    hi = (t.higher_is_better or 0) != 0
    if hi:
        g = t.green_min
        if g is not None:
            return f"≥ {g * 100:.0f}%"
    else:
        g = t.green_max
        if g is not None:
            return f"≤ {g * 100:.0f}%"
    return "—"


def _generator_version_label() -> str:
    if STAFFING_TOOL_VERSION:
        return STAFFING_TOOL_VERSION
    return _generator_version_string()


FILL_WHITE_SOLID = PatternFill(
    start_color=BMF_WHITE, end_color=BMF_WHITE, fill_type="solid"
)
FONT_ZERO_MUTED = Font(name=FONT_NAME, size=11, color=BMF_MEDIUM_GRAY)
FONT_DETAIL_TITLE = Font(name=FONT_NAME, size=16, bold=True, color=BMF_WHITE)
FONT_WEEK_BADGE = Font(name=FONT_NAME, size=10, color=BMF_WHITE)
FONT_LEGEND_LABEL = Font(name=FONT_NAME, size=10, bold=True, color=BMF_NAVY)


def _iso_week_label(week_start: str) -> str:
    d = datetime.strptime(week_start, "%Y-%m-%d").date()
    ic = d.isocalendar()
    week = ic.week if hasattr(ic, "week") else ic[1]
    year = ic.year if hasattr(ic, "year") else ic[0]
    return f"Week {week} · {year}"


def _detail_section_banner(ws, row: int, title: str, subtitle: str) -> None:
    """Weekly_Detail: merge A:F (title) + G (subtitle), navy bar."""
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    a = ws.cell(row, 1, title)
    a.fill = FILL_BMF_NAVY
    a.font = Font(name=FONT_NAME, size=12, bold=True, color=BMF_WHITE)
    a.alignment = ALIGN_LEFT
    a.border = THIN_BORDER
    g = ws.cell(row, 7, subtitle)
    g.fill = FILL_BMF_NAVY
    g.font = Font(name=FONT_NAME, size=10, italic=True, color=BMF_MEDIUM_GRAY)
    g.alignment = ALIGN_RIGHT
    g.border = THIN_BORDER


def _border_right_sep_cell(ws, row: int, col: int) -> None:
    """§5.5: thin vertical separator on the right edge of `col`."""
    c = ws.cell(row, col)
    prev = c.border
    c.border = Border(
        left=prev.left if prev else THIN_BORDER.left,
        right=SIDE_SEP,
        top=prev.top if prev else THIN_BORDER.top,
        bottom=prev.bottom if prev else THIN_BORDER.bottom,
    )


def _write_na_or_int(ws, row: int, col: int, configured: bool, n: int) -> None:
    """§4.1: unconfigured base/unit/shift cells show gray italic N/A."""
    if not configured:
        c = _bmf_cell_border(ws, row, col, "N/A", align=ALIGN_CENTER)
        c.font = FONT_NA
    else:
        _bmf_cell_border(ws, row, col, n, align=ALIGN_CENTER)


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _generator_version_string() -> str:
    try:
        root = _project_root()
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


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
DETAIL_BASE_ORDER = ["Bedford", "Lawrence", "Manchester", "Mansfield", "Plymouth"]

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
        takeaways.append("Overall staffing rate is on target.")
    elif overall == "Yellow":
        takeaways.append(
            "Overall staffing rate is below target; status is Monitor."
        )
    else:
        takeaways.append(
            "Overall staffing rate is below acceptable level; status is Action needed."
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
        ot_ceil = ot_action_ceiling(thresholds)
        exc_monitor = shift_exception_monitor_ceiling(thresholds)
        if ot_now > ot_ceil and ot_now > ot_prior:
            drivers.append(
                f"OT dependency increased ({ot_prior:.1%} → {ot_now:.1%}); overtime filling gaps."
            )
        if this_week.leave_exposure > exc_monitor:
            drivers.append(
                f"Shift exception % at {this_week.leave_exposure:.1%}; contributes to coverage pressure."
            )
        if this_week.ot_dependency > ot_ceil:
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
    rollups_4w: PeriodRollups | None,
    rollups_12w: PeriodRollups | None,
    trend_list: list[tuple[str, WeekMetrics]],
    thresholds: dict[str, KpiThreshold],
    narrative: dict[str, list[str]],
    metadata: dict | None = None,
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
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=7)
        t = ws.cell(row, 1, "Weekly staffing summary")
        t.font = Font(name=FONT_NAME, bold=True, size=14, color=BMF_BLACK)
        t.alignment = ALIGN_LEFT
        row += 1
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=7)
        ws.cell(row, 1, f"Period: {week_start} to {week_end}").font = FONT_BMF_BODY
        row += 2

    # KPI Panel: (display_label, internal_metric_name)
    board_metrics = [
        ("Staffing Rate", "Staffing Rate"),
        ("Backfill Rate", "OT Dependency"),
        ("Shift Exception %", "Shift Exception %"),
        ("System RW Coverage %", "System RW Coverage %"),
        ("System GR Coverage %", "System GR Coverage %"),
    ]
    headers = [
        "Metric",
        "This Week",
        "Prior Week",
        "4-Week Avg",
        "4-Week Pooled",
        "12-Week Avg",
        "12-Week Pooled",
        "Target",
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
        val_4_pool = (
            get_pooled_metric_value(rollups_4w, metric_key) if rollups_4w else None
        )
        val_12 = get_metric_value(avg_12w, metric_key) if avg_12w else None
        val_12_pool = (
            get_pooled_metric_value(rollups_12w, metric_key) if rollups_12w else None
        )
        rag = (
            _rag_for_metric(metric_key, val_this or 0, thresholds)
            if val_this is not None
            else "Green"
        )
        direction = direction_for_metric(metric_key, val_this or 0, val_prior)
        thr = thresholds.get(metric_key)
        notable = _kpi_notable(metric_key, val_this or 0, this_metrics)
        ws.cell(row, 1, display_name).border = THIN_BORDER
        _write_pct_or_num(ws, row, 2, val_this, metric_key)
        _write_pct_or_num(ws, row, 3, val_prior, metric_key)
        _write_pct_or_num(ws, row, 4, val_4, metric_key)
        _write_pct_or_num(ws, row, 5, val_4_pool, metric_key)
        _write_pct_or_num(ws, row, 6, val_12, metric_key)
        _write_pct_or_num(ws, row, 7, val_12_pool, metric_key)
        tcell = ws.cell(row, 8, _target_display(metric_key, thr))
        tcell.border = THIN_BORDER
        status_cell = ws.cell(row, 9, _status_display(rag))
        status_cell.border = THIN_BORDER
        fill, font = _fill_and_font_for_status(
            rag,
            notable=notable,
            value=val_this or 0,
            thr=thr,
        )
        status_cell.fill = fill
        status_cell.font = font
        ws.cell(row, 10, direction).border = THIN_BORDER
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
            _write_pct_cell(
                ws,
                row,
                2,
                rw_pct,
                rw_rag,
                thr=t_rw,
                notable=_base_rw_cell_notable(rw_pct),
            )
            _write_pct_cell(
                ws,
                row,
                3,
                gr_pct,
                gr_rag,
                thr=t_gr,
                notable=_base_gr_cell_notable(gr_pct),
            )
            ws.cell(row, 4, notes)
            row += 1

    row += 1
    meta = metadata or {}
    gen_iso = meta.get("generated_utc")
    if not gen_iso:
        gen_iso = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    src = meta.get("source_filename")
    src_s = src if src else "(unknown)"
    nrows = meta.get("source_rows")
    nrow_s = str(nrows) if nrows is not None else "(unknown)"
    ver = meta.get("generator_version") or _generator_version_label()
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=7)
    fc = ws.cell(
        row,
        1,
        f"Generated: {gen_iso}\n"
        f"Generator version: {ver}\n"
        f"Source: {src_s}\n"
        f"Source rows: {nrow_s}",
    )
    fc.font = FONT_FOOTER_META
    fc.alignment = Alignment(wrap_text=True, vertical="top")

    # Column widths and freeze (keep KPI header row visible when scrolling)
    ws.column_dimensions["A"].width = 28
    for c in range(2, 9):
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


def _write_pct_cell(
    ws,
    row: int,
    col: int,
    value: float,
    rag: RAG,
    *,
    thr: KpiThreshold | None = None,
    notable: bool = False,
) -> None:
    cell = ws.cell(row, col)
    cell.value = value
    cell.number_format = "0.0%"
    cell.border = THIN_BORDER
    cell.alignment = ALIGN_CENTER
    fill, font = _fill_and_font_for_status(
        rag, notable=notable, value=value, thr=thr
    )
    cell.fill = fill
    cell.font = font


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
    """Weekly_Detail: fixed cell map (columns A–G: label, Day, Night, Total, Target, Status, Notes)."""
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
    thresholds = thresholds if thresholds is not None else {}
    thr_sr = thresholds.get("Staffing Rate")
    thr_exc = thresholds.get("Shift Exception %")
    thr_ot = thresholds.get("OT Dependency")
    t_rw = thresholds.get("System RW Coverage %")
    t_gr = thresholds.get("System GR Coverage %")

    medic_u = getattr(row_data, "medic_unpartnered", 0) or 0
    rn_u = getattr(row_data, "rn_unpartnered_staff", 0) or 0
    note_medic = (getattr(row_data, "unpartnered_note_medic", None) or "").strip()
    note_rn = (getattr(row_data, "unpartnered_note_rn", None) or "").strip()

    ot_rn_day = getattr(row_data, "ot_rn_day", 0) or 0
    ot_rn_night = getattr(row_data, "ot_rn_night", 0) or 0
    ot_medic_day = getattr(row_data, "ot_medic_day", 0) or 0
    ot_medic_night = getattr(row_data, "ot_medic_night", 0) or 0
    ot_emt_day = getattr(row_data, "ot_emt_day", 0) or 0
    ot_emt_night = getattr(row_data, "ot_emt_night", 0) or 0
    total_ot_day = ot_rn_day + ot_medic_day + ot_emt_day
    total_ot_night = ot_rn_night + ot_medic_night + ot_emt_night
    ot_dep = this_metrics.ot_dependency

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

    def _exc_count(role: str, keys: list[str]) -> int:
        return _exc_count_breakdown(breakdown, role, keys)

    def _exec_striped(r: int) -> bool:
        return ((r - 7) % 2) == 1

    def _ot_striped(r: int) -> bool:
        return ((r - 18) % 2) == 1

    def _exc_striped(r: int) -> bool:
        return ((r - 26) % 2) == 1

    def _base_striped(r: int) -> bool:
        return ((r - 34) % 2) == 1

    def _row_bg(r: int, striped: bool) -> PatternFill:
        return FILL_BAND_ALT if striped else FILL_WHITE_SOLID

    def _paint_row(
        r: int, c1: int, c2: int, striped: bool, border: bool = True
    ) -> None:
        bg = _row_bg(r, striped)
        for c in range(c1, c2 + 1):
            cell = ws.cell(r, c)
            cell.fill = bg
            if border:
                cell.border = THIN_BORDER

    def _zero_font(n: int) -> Font:
        return FONT_ZERO_MUTED if n == 0 else FONT_BMF_BODY

    # --- Row 1–2: title & period (pinned merges) ---
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 18
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
    ws.merge_cells(start_row=1, start_column=5, end_row=1, end_column=7)
    t1 = ws.cell(1, 1, "Boston MedFlight — Weekly Staffing Detail")
    t1.font = FONT_DETAIL_TITLE
    t1.fill = FILL_BMF_NAVY
    t1.alignment = ALIGN_LEFT
    t1.border = THIN_BORDER
    wk = ws.cell(1, 5, _iso_week_label(week_start))
    wk.font = FONT_WEEK_BADGE
    wk.fill = FILL_BMF_NAVY
    wk.alignment = ALIGN_RIGHT
    wk.border = THIN_BORDER

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=4)
    ws.merge_cells(start_row=2, start_column=5, end_row=2, end_column=7)
    p2 = ws.cell(
        2,
        1,
        f"Reporting period: Sunday {week_start} — Saturday {week_end}",
    )
    p2.font = Font(name=FONT_NAME, size=10, color=BMF_NAVY)
    p2.fill = FILL_BMF_GRAY_BG
    p2.alignment = ALIGN_LEFT
    p2.border = THIN_BORDER
    g2 = ws.cell(
        2,
        5,
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d')}",
    )
    g2.font = Font(name=FONT_NAME, size=10, color=BMF_NAVY)
    g2.fill = FILL_BMF_GRAY_BG
    g2.alignment = ALIGN_RIGHT
    g2.border = THIN_BORDER

    # --- Row 3: legend ---
    a3 = ws.cell(3, 1, "Status legend:")
    a3.font = FONT_LEGEND_LABEL
    a3.fill = FILL_BMF_GRAY_BG
    a3.alignment = ALIGN_RIGHT
    a3.border = THIN_BORDER
    b3 = ws.cell(3, 2, "On target")
    b3.font = Font(name=FONT_NAME, size=10, bold=True, color=BMF_BLACK)
    b3.fill = FILL_GREEN_FULL
    b3.alignment = ALIGN_CENTER
    b3.border = THIN_BORDER
    c3 = ws.cell(3, 3, "Monitor")
    c3.font = Font(name=FONT_NAME, size=10, bold=True, color=BMF_BLACK)
    c3.fill = FILL_YELLOW
    c3.alignment = ALIGN_CENTER
    c3.border = THIN_BORDER
    d3 = ws.cell(3, 4, "Action needed")
    d3.font = FONT_BMF_RAG_VALUE_ON_RED
    d3.fill = FILL_RED_FULL
    d3.alignment = ALIGN_CENTER
    d3.border = THIN_BORDER
    e3 = ws.cell(3, 5, "N/A = unit not staffed at that base/shift")
    e3.font = Font(name=FONT_NAME, size=10, italic=True, color="666666")
    e3.fill = FILL_BMF_GRAY_BG
    e3.alignment = ALIGN_LEFT
    e3.border = THIN_BORDER
    ws.merge_cells(start_row=3, start_column=6, end_row=3, end_column=7)
    fg3 = ws.cell(3, 6)
    fg3.fill = FILL_BMF_GRAY_BG
    fg3.border = THIN_BORDER

    # --- Row 4: spacer ---
    for c in range(1, 8):
        ws.cell(4, c).fill = FILL_WHITE_SOLID
        ws.cell(4, c).border = THIN_BORDER

    # --- Row 5: Executive summary banner ---
    _detail_section_banner(ws, 5, "Executive summary", "This week vs target")

    # --- Row 6: column headers ---
    hdr_fill = FILL_BMF_GRAY_BG
    h6 = [
        (1, "Metric", ALIGN_LEFT),
        (2, "Day", ALIGN_CENTER),
        (3, "Night", ALIGN_CENTER),
        (4, "Total", ALIGN_CENTER),
        (5, "Target", ALIGN_CENTER),
        (6, "Status", ALIGN_CENTER),
        (7, "Notes", ALIGN_LEFT),
    ]
    for col, text, al in h6:
        cell = ws.cell(6, col, text)
        cell.font = BOLD
        cell.fill = hdr_fill
        cell.border = THIN_BORDER
        cell.alignment = al

    # --- Executive data rows 7–14 ---
    sr_day = (
        this_metrics.filled_day / this_metrics.required_day
        if this_metrics.required_day
        else 0.0
    )
    sr_night = (
        this_metrics.filled_night / this_metrics.required_night
        if this_metrics.required_night
        else 0.0
    )
    sr_rag = _rag_for_metric("Staffing Rate", this_metrics.staffing_rate, thresholds)
    sr_notable = _kpi_notable(
        "Staffing Rate", this_metrics.staffing_rate, this_metrics
    )
    exc_rag = _rag_for_metric("Shift Exception %", display_leave_exp, thresholds)
    exc_notable = _kpi_notable(
        "Shift Exception %", display_leave_exp, this_metrics
    )
    ot_rag = _rag_for_metric("OT Dependency", ot_dep, thresholds)
    ot_notable = _kpi_notable("OT Dependency", ot_dep, this_metrics)

    ws.merge_cells(start_row=13, start_column=1, end_row=13, end_column=3)

    for r in range(7, 15):
        st = _exec_striped(r)
        _paint_row(r, 1, 7, st)

    # Row 7 Required shifts
    ws.cell(7, 1, "Required shifts").font = FONT_BMF_BODY
    ws.cell(7, 1).alignment = ALIGN_LEFT
    ws.cell(7, 2, this_metrics.required_day).alignment = ALIGN_CENTER
    ws.cell(7, 3, this_metrics.required_night).alignment = ALIGN_CENTER
    d7 = ws.cell(7, 4, this_metrics.required_total)
    d7.font = FONT_BMF_BODY_BOLD
    d7.alignment = ALIGN_CENTER
    for c in (5, 6, 7):
        ws.cell(7, c, None)

    # Row 8 Filled shifts
    ws.cell(8, 1, "Filled shifts").font = FONT_BMF_BODY
    ws.cell(8, 1).alignment = ALIGN_LEFT
    ws.cell(8, 2, this_metrics.filled_day).alignment = ALIGN_CENTER
    ws.cell(8, 3, this_metrics.filled_night).alignment = ALIGN_CENTER
    d8 = ws.cell(8, 4, this_metrics.filled_total)
    d8.font = FONT_BMF_BODY_BOLD
    d8.alignment = ALIGN_CENTER
    for c in (5, 6, 7):
        ws.cell(8, c, None)

    # Row 9 Staffing rate
    ws.cell(9, 1, "Staffing rate").font = FONT_BMF_BODY_BOLD
    ws.cell(9, 1).alignment = ALIGN_LEFT
    b9 = ws.cell(9, 2, sr_day)
    b9.number_format = "0.0%"
    b9.alignment = ALIGN_CENTER
    c9 = ws.cell(9, 3, sr_night)
    c9.number_format = "0.0%"
    c9.alignment = ALIGN_CENTER
    d9 = ws.cell(9, 4, this_metrics.staffing_rate)
    d9.number_format = "0.0%"
    d9.font = FONT_BMF_RAG_VALUE
    d9.alignment = ALIGN_CENTER
    dfill, dfont = _fill_and_font_for_status(
        sr_rag, notable=sr_notable, value=this_metrics.staffing_rate, thr=thr_sr
    )
    d9.fill = dfill
    d9.font = dfont
    e9 = ws.cell(9, 5, _target_display("Staffing Rate", thr_sr))
    e9.font = Font(name=FONT_NAME, size=11, color="666666")
    e9.number_format = "@"
    e9.alignment = ALIGN_CENTER
    f9 = ws.cell(9, 6, _status_display(sr_rag))
    f9.font = FONT_BMF_RAG_VALUE
    f9.alignment = ALIGN_CENTER
    ff9, ffont9 = _fill_and_font_for_status(
        sr_rag, notable=sr_notable, value=this_metrics.staffing_rate, thr=thr_sr
    )
    f9.fill = ff9
    f9.font = ffont9
    ws.cell(9, 7, None)

    # Row 10 Vacancies
    ws.cell(10, 1, "Vacancies").font = FONT_BMF_BODY
    ws.cell(10, 1).alignment = ALIGN_LEFT
    ws.cell(10, 2, None)
    ws.cell(10, 3, None)
    d10 = ws.cell(10, 4, this_metrics.vacancies)
    d10.font = FONT_BMF_BODY_BOLD
    d10.alignment = ALIGN_CENTER
    for c in (5, 6, 7):
        ws.cell(10, c, None)

    # Row 11 Shift exceptions (total)
    ws.cell(11, 1, "Shift exceptions (total)").font = FONT_BMF_BODY
    ws.cell(11, 1).alignment = ALIGN_LEFT
    ws.cell(11, 2, None)
    ws.cell(11, 3, None)
    d11 = ws.cell(11, 4, display_leave_total)
    d11.font = FONT_BMF_BODY_BOLD
    d11.alignment = ALIGN_CENTER
    for c in (5, 6, 7):
        ws.cell(11, c, None)

    # Row 12 Shift exception %
    ws.cell(12, 1, "Shift exception %").font = FONT_BMF_BODY_BOLD
    ws.cell(12, 1).alignment = ALIGN_LEFT
    ws.cell(12, 2, None)
    ws.cell(12, 3, None)
    d12 = ws.cell(12, 4, display_leave_exp)
    d12.number_format = "0.0%"
    d12.font = FONT_BMF_RAG_VALUE
    d12.alignment = ALIGN_CENTER
    df12, dt12 = _fill_and_font_for_status(
        exc_rag,
        notable=exc_notable,
        value=display_leave_exp,
        thr=thr_exc,
    )
    d12.fill = df12
    d12.font = dt12
    e12 = ws.cell(12, 5, _target_display("Shift Exception %", thr_exc))
    e12.font = Font(name=FONT_NAME, size=11, color="666666")
    e12.number_format = "@"
    e12.alignment = ALIGN_CENTER
    f12 = ws.cell(12, 6, _status_display(exc_rag))
    ff12, ft12 = _fill_and_font_for_status(
        exc_rag,
        notable=exc_notable,
        value=display_leave_exp,
        thr=thr_exc,
    )
    f12.fill = ff12
    f12.font = ft12
    f12.alignment = ALIGN_CENTER
    ws.cell(12, 7, None)

    # Row 13 Unpartnered Medic (A:C merged above)
    ws.cell(13, 1, "Unpartnered — Medic").font = FONT_BMF_BODY_BOLD
    ws.cell(13, 1).alignment = ALIGN_LEFT
    d13 = ws.cell(13, 4, medic_u)
    d13.font = FONT_BMF_BODY_BOLD
    d13.alignment = ALIGN_CENTER
    for c in (5, 6):
        ws.cell(13, c, None)
    g13 = ws.cell(13, 7, note_medic)
    g13.font = Font(name=FONT_NAME, size=11, color="666666")
    g13.alignment = ALIGN_LEFT

    # Row 14 Unpartnered RN
    ws.cell(14, 1, "Unpartnered — RN").font = FONT_BMF_BODY_BOLD
    ws.cell(14, 1).alignment = ALIGN_LEFT
    ws.cell(14, 2, None)
    ws.cell(14, 3, None)
    ws.cell(14, 4, rn_u).alignment = ALIGN_CENTER
    for c in (5, 6):
        ws.cell(14, c, None)
    g14 = ws.cell(14, 7, note_rn)
    g14.font = Font(name=FONT_NAME, size=11, italic=True, color="666666")
    g14.alignment = ALIGN_LEFT

    # Row 15 spacer
    for c in range(1, 8):
        ws.cell(15, c).fill = FILL_WHITE_SOLID
        ws.cell(15, c).border = THIN_BORDER

    # --- Section 2 Overtime rows 16–23 ---
    _detail_section_banner(ws, 16, "Overtime", "Shift counts")
    h_ot = [
        (1, "Role", ALIGN_LEFT),
        (2, "Day", ALIGN_CENTER),
        (3, "Night", ALIGN_CENTER),
        (4, "Total", ALIGN_CENTER),
        (5, "Target", ALIGN_CENTER),
        (6, "Status", ALIGN_CENTER),
        (7, "Notes", ALIGN_LEFT),
    ]
    for col, text, al in h_ot:
        cell = ws.cell(17, col, text)
        cell.font = BOLD
        cell.fill = hdr_fill
        cell.border = THIN_BORDER
        cell.alignment = al

    ot_rows = [
        ("RN", ot_rn_day, ot_rn_night),
        ("Medic", ot_medic_day, ot_medic_night),
        ("EMT", ot_emt_day, ot_emt_night),
    ]
    r = 18
    for label, bd, bn in ot_rows:
        st = _ot_striped(r)
        _paint_row(r, 1, 7, st)
        ws.cell(r, 1, label).font = FONT_BMF_BODY_BOLD
        ws.cell(r, 1).alignment = ALIGN_LEFT
        b = ws.cell(r, 2, bd)
        b.font = _zero_font(bd)
        b.alignment = ALIGN_CENTER
        c = ws.cell(r, 3, bn)
        c.font = _zero_font(bn)
        c.alignment = ALIGN_CENTER
        ws.cell(r, 4, bd + bn).alignment = ALIGN_CENTER
        for cc in (5, 6, 7):
            ws.cell(r, cc, None)
        r += 1

    # Row 21 Total OT
    for c in range(1, 8):
        cell = ws.cell(21, c)
        cell.fill = FILL_BAND_TOTAL
        cell.border = THIN_BORDER
        cell.font = FONT_BMF_BODY_BOLD
    ws.cell(21, 1, "Total").alignment = ALIGN_LEFT
    ws.cell(21, 2, total_ot_day).alignment = ALIGN_CENTER
    ws.cell(21, 3, total_ot_night).alignment = ALIGN_CENTER
    ws.cell(21, 4, total_ot_day + total_ot_night).alignment = ALIGN_CENTER
    for c in (5, 6, 7):
        ws.cell(21, c, None)

    # Row 22 Backfill rate
    _paint_row(22, 1, 7, False)
    ws.cell(22, 1, "Backfill rate (OT / filled)").font = FONT_BMF_BODY_BOLD
    ws.cell(22, 1).alignment = ALIGN_LEFT
    ws.cell(22, 2, None)
    ws.cell(22, 3, None)
    d22 = ws.cell(22, 4, ot_dep)
    d22.number_format = "0.0%"
    d22.font = FONT_BMF_RAG_VALUE
    d22.alignment = ALIGN_CENTER
    df22, dt22 = _fill_and_font_for_status(
        ot_rag, notable=ot_notable, value=ot_dep, thr=thr_ot
    )
    d22.fill = df22
    d22.font = dt22
    e22 = ws.cell(22, 5, _target_display("OT Dependency", thr_ot))
    e22.font = Font(name=FONT_NAME, size=11, color="666666")
    e22.number_format = "@"
    e22.alignment = ALIGN_CENTER
    f22 = ws.cell(22, 6, _status_display(ot_rag))
    ff22, ft22 = _fill_and_font_for_status(
        ot_rag, notable=ot_notable, value=ot_dep, thr=thr_ot
    )
    f22.fill = ff22
    f22.font = ft22
    f22.alignment = ALIGN_CENTER
    ws.cell(22, 7, None)

    # Row 23 spacer
    for c in range(1, 8):
        ws.cell(23, c).fill = FILL_WHITE_SOLID
        ws.cell(23, c).border = THIN_BORDER

    # --- Section 3 Exceptions rows 24–31 ---
    _detail_section_banner(
        ws, 24, "Schedule exceptions by role", "Shift counts by type"
    )
    ws.cell(25, 1, "Role").font = BOLD
    ws.cell(25, 1).fill = hdr_fill
    ws.cell(25, 1).border = THIN_BORDER
    ws.cell(25, 1).alignment = ALIGN_LEFT
    for ci, lt in enumerate(LEAVE_TYPE_COLS, start=2):
        cell = ws.cell(25, ci, lt)
        cell.font = BOLD
        cell.fill = hdr_fill
        cell.border = THIN_BORDER
        cell.alignment = ALIGN_CENTER

    role_labels = EXCEPTION_ROLES
    rr = 26
    for role in role_labels:
        st = _exc_striped(rr)
        _paint_row(rr, 1, 7, st)
        ws.cell(rr, 1, role).font = FONT_BMF_BODY_BOLD
        ws.cell(rr, 1).alignment = ALIGN_LEFT
        vals = [
            _exc_count(role, ["AT"]),
            _exc_count(role, ["LT-D", "LT-N", "LT"]),
            _exc_count(role, ["SICK"]),
            _exc_count(role, ["LOA", "PFML"]),
            _exc_count(role, ["JURY"]),
            _exc_count(role, ["BREV"]),
        ]
        for j, v in enumerate(vals, start=2):
            cell = ws.cell(rr, j, v)
            cell.alignment = ALIGN_CENTER
            cell.font = _zero_font(v)
        rr += 1

    for c in range(1, 8):
        cell = ws.cell(30, c)
        cell.fill = FILL_BAND_TOTAL
        cell.border = THIN_BORDER
        cell.font = FONT_BMF_BODY_BOLD
    ws.cell(30, 1, "Total").alignment = ALIGN_LEFT
    for j, tot in enumerate(col_totals, start=2):
        cell = ws.cell(30, j, tot)
        cell.alignment = ALIGN_CENTER
        if j in (6, 7) and tot == 0:
            cell.font = FONT_ZERO_MUTED
        else:
            cell.font = FONT_BMF_BODY_BOLD

    # Row 31 spacer
    for c in range(1, 8):
        ws.cell(31, c).fill = FILL_WHITE_SOLID
        ws.cell(31, c).border = THIN_BORDER

    # --- Section 4 Base coverage ---
    _detail_section_banner(ws, 32, "Base coverage", "Rotor-Wing / Ground")
    base_headers = [
        "Base",
        "RW/D (of 7)",
        "RW/N (of 7)",
        "GR/D (of 7)",
        "GR/N (of 7)",
        "RW %",
        "GR %",
    ]
    for c, h in enumerate(base_headers, start=1):
        cell = ws.cell(33, c, h)
        cell.font = BOLD
        cell.fill = hdr_fill
        cell.border = THIN_BORDER
        cell.alignment = ALIGN_CENTER if c > 1 else ALIGN_LEFT
    _border_right_sep_cell(ws, 33, 3)
    _border_right_sep_cell(ws, 33, 5)

    row_num = 34
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
        umap = BASE_UNIT_CELL_CONFIGURED.get(base_name, {})
        st = _base_striped(row_num)
        _paint_row(row_num, 1, 7, st)
        ws.cell(row_num, 1, base_name).font = FONT_BMF_BODY_BOLD
        ws.cell(row_num, 1).alignment = ALIGN_LEFT
        _write_na_or_int(ws, row_num, 2, umap.get("rw_d", False), rw_d)
        _write_na_or_int(ws, row_num, 3, umap.get("rw_n", False), rw_n)
        _write_na_or_int(ws, row_num, 4, umap.get("gr_d", False), gr_d)
        _write_na_or_int(ws, row_num, 5, umap.get("gr_n", False), gr_n)
        if rw_total:
            rw_pct_val = rw_staffed / rw_total
            rw_rag = evaluate_rag(rw_pct_val, t_rw) if t_rw else "Green"
            c6 = ws.cell(row_num, 6, rw_pct_val)
            c6.number_format = "0.0%"
            c6.alignment = ALIGN_CENTER
            c6.border = THIN_BORDER
            fill6, font6 = _fill_and_font_for_status(
                rw_rag,
                notable=_base_rw_cell_notable(rw_pct_val),
                value=rw_pct_val,
                thr=t_rw,
            )
            c6.fill = fill6
            c6.font = font6
        else:
            c6 = ws.cell(row_num, 6, "N/A")
            c6.font = FONT_NA
            c6.alignment = ALIGN_CENTER
            c6.border = THIN_BORDER
        if gr_total:
            gr_pct_val = gr_staffed / gr_total
            gr_rag = evaluate_rag(gr_pct_val, t_gr) if t_gr else "Green"
            c7 = ws.cell(row_num, 7, gr_pct_val)
            c7.number_format = "0.0%"
            c7.alignment = ALIGN_CENTER
            c7.border = THIN_BORDER
            fill7, font7 = _fill_and_font_for_status(
                gr_rag,
                notable=_base_gr_cell_notable(gr_pct_val),
                value=gr_pct_val,
                thr=t_gr,
            )
            c7.fill = fill7
            c7.font = font7
        else:
            c7 = ws.cell(row_num, 7, "N/A")
            c7.font = FONT_NA
            c7.alignment = ALIGN_CENTER
            c7.border = THIN_BORDER
        _border_right_sep_cell(ws, row_num, 3)
        _border_right_sep_cell(ws, row_num, 5)
        row_num += 1

    for c in range(1, 8):
        cell = ws.cell(row_num, c)
        cell.fill = FILL_BAND_TOTAL
        cell.border = THIN_BORDER
        cell.font = FONT_BMF_BODY_BOLD
    ws.cell(row_num, 1, "System total").alignment = ALIGN_LEFT
    # §3.3: do not sum per-base raw counts (B–E); system view is weighted % only.
    for c in range(2, 6):
        cell = ws.cell(row_num, c)
        cell.value = None
        cell.alignment = ALIGN_CENTER
    sys_rw_rag = _rag_for_metric(
        "System RW Coverage %", this_metrics.system_rw_pct, thresholds
    )
    sys_rw_notable = _kpi_notable(
        "System RW Coverage %", this_metrics.system_rw_pct, this_metrics
    )
    _write_pct_cell(
        ws,
        row_num,
        6,
        this_metrics.system_rw_pct,
        sys_rw_rag,
        thr=t_rw,
        notable=sys_rw_notable,
    )
    sys_gr_rag = _rag_for_metric(
        "System GR Coverage %", this_metrics.system_gr_pct, thresholds
    )
    sys_gr_notable = _kpi_notable(
        "System GR Coverage %", this_metrics.system_gr_pct, this_metrics
    )
    _write_pct_cell(
        ws,
        row_num,
        7,
        this_metrics.system_gr_pct,
        sys_gr_rag,
        thr=t_gr,
        notable=sys_gr_notable,
    )
    _border_right_sep_cell(ws, row_num, 3)
    _border_right_sep_cell(ws, row_num, 5)

    ws.freeze_panes = "A5"
    ws.column_dimensions["A"].width = 28
    for letter, w in [("B", 12), ("C", 12), ("D", 12), ("E", 12), ("F", 14), ("G", 28)]:
        ws.column_dimensions[letter].width = w


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
    thr_sr = thresholds.get("Staffing Rate")
    row = 2
    for week_start, m in trend_list:
        ws.cell(row, 1, week_start)
        ws.cell(row, 2, m.staffing_rate).number_format = "0.0%"
        ws.cell(row, 3, m.ot_dependency).number_format = "0.0%"
        ws.cell(row, 4, m.leave_exposure).number_format = "0.0%"
        ws.cell(row, 5, m.system_rw_pct).number_format = "0.0%"
        ws.cell(row, 6, m.system_gr_pct).number_format = "0.0%"
        rag = _rag_for_metric("Staffing Rate", m.staffing_rate, thresholds)
        st = ws.cell(row, 7, _status_display(rag))
        notable = _kpi_notable("Staffing Rate", m.staffing_rate, m)
        fill, font = _fill_and_font_for_status(
            rag,
            notable=notable,
            value=m.staffing_rate,
            thr=thr_sr,
        )
        st.fill = fill
        st.font = font
        st.border = THIN_BORDER
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
    metadata: dict | None = None,
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
        bases_all = session.query(BaseConfig).order_by(BaseConfig.base_name).all()
        _assert_rw_config_rw_cap_56(bases_all)
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
        rollups_4w = compute_period_rollups(last_4) if last_4 else None
        rollups_12w = (
            compute_period_rollups(trend_metrics) if trend_metrics else None
        )

        rag_statuses = {}
        for name in [
            "Staffing Rate",
            "OT Dependency",
            "Shift Exception %",
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
            rollups_4w,
            rollups_12w,
            trend_data,
            thresholds,
            narrative,
            metadata=metadata,
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
