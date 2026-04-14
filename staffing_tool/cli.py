"""
CLI for Boston MedFlight Board-Level Staffing KPI Tool.

Commands: init-db, set-base-totals, show-base-totals, set-threshold,
show-thresholds, upsert-week, upsert-base-coverage, list-weeks, export-week,
export-latest, export-board-pack.
"""

import argparse
import os
import sys
from datetime import UTC, datetime

from .db import (
    create_tables,
    get_engine,
    init_db,
    migrate_add_base_coverage_day_night,
    migrate_system_gr_kpi_thresholds,
    migrate_weekly_staffing_columns,
    session_scope,
)
from .metrics import compute_week_metrics
from .models import (
    BaseConfig,
    KpiThreshold,
    WeeklyBaseCoverage,
    WeeklyLeaveDetail,
    WeeklyStaffing,
    WeeklyStaffingDetail,
)
from .rag import evaluate_rag
from .report import export_board_pack, export_week_excel
from .validation import notes_required

DB_PATH_DEFAULT = "staffing.db"


def _ensure_sunday(week_start: str) -> None:
    """Validate that week_start is a Sunday (week runs Sun–Sat)."""
    d = datetime.strptime(week_start, "%Y-%m-%d")
    # Python: Monday=0, Sunday=6
    if d.weekday() != 6:
        raise ValueError(
            "week_start must be a Sunday (YYYY-MM-DD), got "
            f"{week_start} (weekday={d.weekday()})"
        )


def _cmd_init_db(args: argparse.Namespace, db_path: str) -> None:
    init_db(db_path)
    print(
        "Database initialized: tables created, base_config and "
        "kpi_thresholds seeded."
    )


def _cmd_migrate(args: argparse.Namespace, db_path: str) -> None:
    """Add missing columns/tables (OT, leave, base coverage D/N, etc.).

    Safe for existing DBs.
    """
    engine = get_engine(db_path)
    create_tables(engine)
    migrate_weekly_staffing_columns(engine)
    migrate_add_base_coverage_day_night(engine)
    migrate_system_gr_kpi_thresholds(engine)
    print("Migration complete. Missing columns/tables added.")


def _cmd_set_base_totals(args: argparse.Namespace, db_path: str) -> None:
    base = args.base
    rw = args.rw_total
    gr = args.gr_total
    if rw < 0 or gr < 0:
        raise ValueError("rw_total and gr_total must be >= 0")
    with session_scope(db_path) as session:
        row = session.query(BaseConfig).filter(BaseConfig.base_name == base).first()
        if not row:
            raise ValueError(
                f"Base {base!r} not found. Run init-db first; bases are "
                "Bedford, Lawrence, Mansfield, Manchester, Plymouth."
            )
        row.rw_total_unit_days = rw
        row.gr_total_unit_days = gr
        row.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Updated {base}: rw_total_unit_days={rw}, gr_total_unit_days={gr}")


def _cmd_show_base_totals(args: argparse.Namespace, db_path: str) -> None:
    with session_scope(db_path) as session:
        rows = session.query(BaseConfig).order_by(BaseConfig.base_name).all()
    if not rows:
        print("No base config. Run init-db first.")
        return
    print("Base totals (RW / GR unit-days per week):")
    for r in rows:
        print(
            f"  {r.base_name}: RW={r.rw_total_unit_days}, "
            f"GR={r.gr_total_unit_days}"
        )


def _cmd_set_threshold(args: argparse.Namespace, db_path: str) -> None:
    metric = args.metric
    g_min = getattr(args, "green_min", None)
    g_max = getattr(args, "green_max", None)
    y_min = getattr(args, "yellow_min", None)
    y_max = getattr(args, "yellow_max", None)
    r_min = getattr(args, "red_min", None)
    r_max = getattr(args, "red_max", None)
    with session_scope(db_path) as session:
        row = (
            session.query(KpiThreshold)
            .filter(KpiThreshold.metric_name == metric)
            .first()
        )
        if not row:
            row = KpiThreshold(metric_name=metric, higher_is_better=1)
            session.add(row)
        if g_min is not None:
            row.green_min = g_min
        if g_max is not None:
            row.green_max = g_max
        if y_min is not None:
            row.yellow_min = y_min
        if y_max is not None:
            row.yellow_max = y_max
        if r_min is not None:
            row.red_min = r_min
        if r_max is not None:
            row.red_max = r_max
    print(f"Threshold updated for metric: {metric}")


def _cmd_show_thresholds(args: argparse.Namespace, db_path: str) -> None:
    with session_scope(db_path) as session:
        rows = (
            session.query(KpiThreshold)
            .order_by(KpiThreshold.metric_name)
            .all()
        )
    if not rows:
        print("No thresholds. Run init-db first.")
        return
    print("KPI thresholds:")
    for r in rows:
        print(
            f"  {r.metric_name}: green=[{r.green_min},{r.green_max}] "
            f"yellow=[{r.yellow_min},{r.yellow_max}] "
            f"red=[{r.red_min},{r.red_max}] "
            f"higher_is_better={r.higher_is_better}"
        )


def _cmd_upsert_week(args: argparse.Namespace, db_path: str) -> None:
    week_start = args.week_start
    _ensure_sunday(week_start)
    entered_by = getattr(args, "entered_by", None) or os.environ.get(
        "USERNAME", "unknown"
    )
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    with session_scope(db_path) as session:
        row = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start == week_start)
            .first()
        )
        base_configs = session.query(BaseConfig).all()
        coverages = (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start == week_start)
            .all()
        )

        if row:
            # Partial update: only set provided fields
            if hasattr(args, "day_target") and args.day_target is not None:
                row.day_target = args.day_target
            if hasattr(args, "night_min") and args.night_min is not None:
                row.night_min = args.night_min
            if hasattr(args, "filled_day") and args.filled_day is not None:
                row.filled_day = args.filled_day
            if hasattr(args, "filled_night") and args.filled_night is not None:
                row.filled_night = args.filled_night
            if hasattr(args, "ot_shifts") and args.ot_shifts is not None:
                row.ot_shifts = args.ot_shifts
            if hasattr(args, "ot_rn") and args.ot_rn is not None:
                row.ot_rn = args.ot_rn
            if hasattr(args, "ot_medic") and args.ot_medic is not None:
                row.ot_medic = args.ot_medic
            if hasattr(args, "ot_emt") and args.ot_emt is not None:
                row.ot_emt = args.ot_emt
            if hasattr(args, "leave_at") and args.leave_at is not None:
                row.leave_at = args.leave_at
            if hasattr(args, "leave_lt") and args.leave_lt is not None:
                row.leave_lt = args.leave_lt
            if hasattr(args, "leave_sick") and args.leave_sick is not None:
                row.leave_sick = args.leave_sick
            if hasattr(args, "leave_loa") and args.leave_loa is not None:
                row.leave_loa = args.leave_loa
            if hasattr(args, "leave_pfml") and args.leave_pfml is not None:
                row.leave_pfml = args.leave_pfml
            if hasattr(args, "leave_jury") and args.leave_jury is not None:
                row.leave_jury = args.leave_jury
            if hasattr(args, "leave_brev") and args.leave_brev is not None:
                row.leave_brev = args.leave_brev
            if (
                hasattr(args, "overnights_below")
                and args.overnights_below is not None
            ):
                row.overnights_below = args.overnights_below
            if (
                hasattr(args, "pilot_vacancies")
                and args.pilot_vacancies is not None
            ):
                row.pilot_vacancies = args.pilot_vacancies
            if hasattr(args, "notes") and args.notes is not None:
                row.notes = args.notes
            if hasattr(args, "entered_by") and args.entered_by is not None:
                row.entered_by = args.entered_by
            row.updated_at = now
        else:
            # Insert: require at least filled_day, filled_night
            filled_day = getattr(args, "filled_day", None)
            filled_night = getattr(args, "filled_night", None)
            if filled_day is None or filled_night is None:
                raise ValueError(
                    "New week requires --filled-day and --filled-night"
                )
            total_ot = getattr(args, "ot_shifts", None) or 0
            ot_rn = getattr(args, "ot_rn", None) or 0
            ot_medic = getattr(args, "ot_medic", None) or 0
            ot_emt = getattr(args, "ot_emt", None) or 0
            if ot_rn or ot_medic or ot_emt:
                total_ot = ot_rn + ot_medic + ot_emt

            row = WeeklyStaffing(
                week_start=week_start,
                day_target=getattr(args, "day_target", None) or 8,
                night_min=getattr(args, "night_min", None) or 4,
                filled_day=filled_day,
                filled_night=filled_night,
                ot_shifts=total_ot,
                ot_rn=ot_rn,
                ot_medic=ot_medic,
                ot_emt=ot_emt,
                leave_at=getattr(args, "leave_at", None) or 0,
                leave_lt=getattr(args, "leave_lt", None) or 0,
                leave_sick=getattr(args, "leave_sick", None) or 0,
                leave_loa=getattr(args, "leave_loa", None) or 0,
                leave_pfml=getattr(args, "leave_pfml", None) or 0,
                leave_jury=getattr(args, "leave_jury", None) or 0,
                leave_brev=getattr(args, "leave_brev", None) or 0,
                overnights_below=getattr(args, "overnights_below", None) or 0,
                pilot_vacancies=getattr(args, "pilot_vacancies", None) or 0,
                notes=getattr(args, "notes", None),
                entered_by=entered_by,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        session.flush()

        # Compute metrics to check audit rules
        m = compute_week_metrics(row, coverages, base_configs)
        base_staffed_gt_total = False
        base_by_name = {b.base_name: b for b in base_configs}
        for c in coverages:
            cfg = base_by_name.get(c.base_name)
            if cfg and (
                cfg.rw_total_unit_days > 0
                and c.rw_staffed_unit_days > cfg.rw_total_unit_days
                or cfg.gr_total_unit_days > 0
                and c.gr_staffed_unit_days > cfg.gr_total_unit_days
            ):
                base_staffed_gt_total = True
                break

        if notes_required(
            m.staffing_rate,
            m.ot_dependency,
            m.filled_total,
            required_total=m.required_total,
            base_staffed_gt_total=base_staffed_gt_total,
        ):
            if not (row.notes and row.notes.strip()):
                raise ValueError(
                    "Notes are required when: staffing_rate < 0.90, OT dependency > 0.12, "
                    "any base staffed > total, or filled_total > required_total + 10. Please add --notes."
                )

    print(f"Week {week_start} upserted.")


def _cmd_upsert_base_coverage(args: argparse.Namespace, db_path: str) -> None:
    week_start = args.week_start
    base = args.base
    rw_staffed = getattr(args, "rw_staffed", 0)
    gr_staffed = getattr(args, "gr_staffed", 0)
    _ensure_sunday(week_start)

    with session_scope(db_path) as session:
        cfg = (
            session.query(BaseConfig)
            .filter(BaseConfig.base_name == base)
            .first()
        )
        if not cfg:
            raise ValueError(f"Base {base!r} not found.")
        if cfg.rw_total_unit_days == 0 and rw_staffed > 0:
            raise ValueError(
                f"Base {base} has rw_total_unit_days=0; cannot set "
                f"rw_staffed={rw_staffed}. Set base totals first."
            )
        if cfg.gr_total_unit_days == 0 and gr_staffed > 0:
            raise ValueError(
                f"Base {base} has gr_total_unit_days=0; cannot set "
                f"gr_staffed={gr_staffed}. Set base totals first."
            )

        rec = (
            session.query(WeeklyBaseCoverage)
            .filter(
                WeeklyBaseCoverage.week_start == week_start,
                WeeklyBaseCoverage.base_name == base,
            )
            .first()
        )
        if rec:
            rec.rw_staffed_unit_days = rw_staffed
            rec.gr_staffed_unit_days = gr_staffed
        else:
            session.add(
                WeeklyBaseCoverage(
                    week_start=week_start,
                    base_name=base,
                    rw_staffed_unit_days=rw_staffed,
                    gr_staffed_unit_days=gr_staffed,
                )
            )
        session.flush()

        # Warn if staffed > total (require notes at week level)
        rw_over = (
            cfg.rw_total_unit_days
            and rw_staffed > cfg.rw_total_unit_days
        )
        gr_over = (
            cfg.gr_total_unit_days
            and gr_staffed > cfg.gr_total_unit_days
        )
        if rw_over or gr_over:
            week_row = (
                session.query(WeeklyStaffing)
                .filter(WeeklyStaffing.week_start == week_start)
                .first()
            )
            if week_row and (not week_row.notes or not week_row.notes.strip()):
                print(
                    "Warning: Staffed unit-days exceed base total for this "
                    "base. Ensure week-level notes are set (required for "
                    "export)."
                )

    print(
        f"Base coverage updated: {week_start} / {base}  "
        f"RW={rw_staffed}  GR={gr_staffed}"
    )


def _cmd_delete_week(args: argparse.Namespace, db_path: str) -> None:
    """Remove a week and dependent rows (coverage, leave detail, CEO grid)."""
    week_start = args.week_start
    _ensure_sunday(week_start)
    with session_scope(db_path) as session:
        row = (
            session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start == week_start)
            .first()
        )
        if not row:
            print(f"No data for week {week_start}. Nothing to delete.")
            return
        session.query(WeeklyLeaveDetail).filter(
            WeeklyLeaveDetail.week_start == week_start
        ).delete(synchronize_session=False)
        session.query(WeeklyStaffingDetail).filter(
            WeeklyStaffingDetail.week_start == week_start
        ).delete(synchronize_session=False)
        session.query(WeeklyBaseCoverage).filter(
            WeeklyBaseCoverage.week_start == week_start
        ).delete(synchronize_session=False)
        session.delete(row)
    print(
        f"Deleted week {week_start} (staffing, base coverage, and detail rows)."
    )


def _cmd_list_weeks(args: argparse.Namespace, db_path: str) -> None:
    n = getattr(args, "n", 12) or 12
    with session_scope(db_path) as session:
        week_starts = (
            session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start.desc())
            .limit(n)
            .all()
        )
        week_starts = [w[0] for w in reversed(week_starts)]
        if not week_starts:
            print("No weeks in database.")
            return
        thresholds = {t.metric_name: t for t in session.query(KpiThreshold).all()}
        bases = session.query(BaseConfig).all()
        staffing_by_week = {
            r.week_start: r
            for r in session.query(WeeklyStaffing)
            .filter(WeeklyStaffing.week_start.in_(week_starts))
            .all()
        }
        coverages_by_week = {ws: [] for ws in week_starts}
        for c in (
            session.query(WeeklyBaseCoverage)
            .filter(WeeklyBaseCoverage.week_start.in_(week_starts))
            .all()
        ):
            coverages_by_week[c.week_start].append(c)

        print(
            f"Last {len(week_starts)} weeks "
            "(rate, OT dep, leave exp, overnight below, RAG):"
        )
        for ws in week_starts:
            row = staffing_by_week.get(ws)
            if not row:
                continue
            coverages = coverages_by_week.get(ws, [])
            m = compute_week_metrics(row, coverages, bases)
            th = thresholds.get("Staffing Rate")
            rag = evaluate_rag(m.staffing_rate, th) if th else "—"
            print(
                f"  {ws}  rate={m.staffing_rate:.1%}  "
                f"OT dep={m.ot_dependency:.1%}  "
                f"leave_exp={m.leave_exposure:.1%}  "
                f"overnight_below={m.overnights_below}  RAG={rag}"
            )


def _cmd_export_week(args: argparse.Namespace, db_path: str) -> None:
    week_start = args.week_start
    _ensure_sunday(week_start)
    output_dir = getattr(args, "output_dir", None) or "output"
    path = export_week_excel(db_path, week_start, output_dir=output_dir)
    print(f"Exported: {path}")


def _cmd_export_latest(args: argparse.Namespace, db_path: str) -> None:
    with session_scope(db_path) as session:
        row = (
            session.query(WeeklyStaffing.week_start)
            .order_by(WeeklyStaffing.week_start.desc())
            .first()
        )
    if not row:
        raise ValueError(
            "No weeks in database. Add data with upsert-week first."
        )
    week_start = row[0]
    output_dir = getattr(args, "output_dir", None) or "output"
    path = export_week_excel(db_path, week_start, output_dir=output_dir)
    print(f"Exported latest week {week_start}: {path}")


def _cmd_export_board_pack(args: argparse.Namespace, db_path: str) -> None:
    week_start = getattr(args, "week_start", None)
    if not week_start:
        with session_scope(db_path) as session:
            row = (
                session.query(WeeklyStaffing.week_start)
                .order_by(WeeklyStaffing.week_start.desc())
                .first()
            )
            if not row:
                raise ValueError(
                    "No weeks in database. Specify --week-start or add "
                    "data first."
                )
            week_start = row[0]
    _ensure_sunday(week_start)
    weeks = getattr(args, "weeks", 12) or 12
    output_dir = getattr(args, "output_dir", None) or "output"
    path = export_board_pack(
        db_path, week_start, trend_weeks=weeks, output_dir=output_dir
    )
    print(f"Board pack exported: {path}")


def main() -> None:
    # --db on each subparser: python -m staffing_tool init-db --db path
    db_parent = argparse.ArgumentParser(add_help=False)
    db_parent.add_argument(
        "--db",
        default=DB_PATH_DEFAULT,
        help="Path to staffing.db (default: staffing.db in current directory)",
    )

    parser = argparse.ArgumentParser(
        description="BMF Board-Level Staffing KPI Tool (CLI)",
        epilog=(
            "Example: python -m staffing_tool init-db   or   "
            'python -m staffing_tool init-db --db "C:\\path\\staffing.db"'
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # init-db
    sub.add_parser("init-db", parents=[db_parent])
    # migrate (add missing columns/tables for existing DBs)
    sub.add_parser("migrate", parents=[db_parent])

    # set-base-totals
    p = sub.add_parser("set-base-totals", parents=[db_parent])
    p.add_argument(
        "--base",
        required=True,
        help="Bedford | Lawrence | Mansfield | Manchester | Plymouth",
    )
    p.add_argument("--rw-total", type=int, required=True, dest="rw_total")
    p.add_argument("--gr-total", type=int, required=True, dest="gr_total")

    # show-base-totals
    sub.add_parser("show-base-totals", parents=[db_parent])

    # set-threshold
    p = sub.add_parser("set-threshold", parents=[db_parent])
    p.add_argument("--metric", required=True)
    p.add_argument("--green-min", type=float, dest="green_min", default=None)
    p.add_argument("--green-max", type=float, dest="green_max", default=None)
    p.add_argument("--yellow-min", type=float, dest="yellow_min", default=None)
    p.add_argument("--yellow-max", type=float, dest="yellow_max", default=None)
    p.add_argument("--red-min", type=float, dest="red_min", default=None)
    p.add_argument("--red-max", type=float, dest="red_max", default=None)

    # show-thresholds
    sub.add_parser("show-thresholds", parents=[db_parent])

    # upsert-week
    p = sub.add_parser("upsert-week", parents=[db_parent])
    p.add_argument("--week-start", required=True, dest="week_start")
    p.add_argument("--day-target", type=int, dest="day_target", default=None)
    p.add_argument("--night-min", type=int, dest="night_min", default=None)
    p.add_argument(
        "--filled-day", type=int, dest="filled_day", default=None
    )
    p.add_argument(
        "--filled-night", type=int, dest="filled_night", default=None
    )
    # OT can be entered either as a total or by role.
    p.add_argument(
        "--ot-shifts",
        type=int,
        dest="ot_shifts",
        default=None,
        help="Total OT shifts (all roles)",
    )
    p.add_argument(
        "--ot-rn", type=int, dest="ot_rn", default=None, help="OT shifts (RN)"
    )
    p.add_argument(
        "--ot-medic",
        type=int,
        dest="ot_medic",
        default=None,
        help="OT shifts (Medic)",
    )
    p.add_argument(
        "--ot-emt",
        type=int,
        dest="ot_emt",
        default=None,
        help="OT shifts (EMT)",
    )
    p.add_argument("--leave-at", type=int, dest="leave_at", default=None)
    p.add_argument("--leave-lt", type=int, dest="leave_lt", default=None)
    p.add_argument("--leave-sick", type=int, dest="leave_sick", default=None)
    p.add_argument("--leave-loa", type=int, dest="leave_loa", default=None)
    p.add_argument("--leave-pfml", type=int, dest="leave_pfml", default=None)
    p.add_argument("--leave-jury", type=int, dest="leave_jury", default=None)
    p.add_argument("--leave-brev", type=int, dest="leave_brev", default=None)
    p.add_argument(
        "--overnights-below",
        type=int,
        dest="overnights_below",
        default=None,
    )
    p.add_argument("--pilot-vacancies", type=int, dest="pilot_vacancies", default=None)
    p.add_argument("--notes", dest="notes", default=None)
    p.add_argument("--entered-by", dest="entered_by", default=None)

    # upsert-base-coverage
    p = sub.add_parser("upsert-base-coverage", parents=[db_parent])
    p.add_argument("--week-start", required=True, dest="week_start")
    p.add_argument("--base", required=True)
    p.add_argument("--rw-staffed", type=int, dest="rw_staffed", default=0)
    p.add_argument("--gr-staffed", type=int, dest="gr_staffed", default=0)

    # delete-week (remove test weeks so trends are accurate)
    p = sub.add_parser("delete-week", parents=[db_parent])
    p.add_argument(
        "--week-start",
        required=True,
        dest="week_start",
        help="Sunday YYYY-MM-DD",
    )

    # list-weeks
    p = sub.add_parser("list-weeks", parents=[db_parent])
    p.add_argument("--n", type=int, default=12)

    # export-week
    p = sub.add_parser("export-week", parents=[db_parent])
    p.add_argument("--week-start", required=True, dest="week_start")
    p.add_argument("--output-dir", dest="output_dir", default="output")

    # export-latest
    p = sub.add_parser("export-latest", parents=[db_parent])
    p.add_argument("--output-dir", dest="output_dir", default="output")

    # export-board-pack
    p = sub.add_parser("export-board-pack", parents=[db_parent])
    p.add_argument("--week-start", dest="week_start", default=None)
    p.add_argument("--weeks", type=int, default=12)
    p.add_argument("--output-dir", dest="output_dir", default="output")

    args = parser.parse_args()
    db_path = args.db

    commands = {
        "init-db": _cmd_init_db,
        "migrate": _cmd_migrate,
        "set-base-totals": _cmd_set_base_totals,
        "show-base-totals": _cmd_show_base_totals,
        "set-threshold": _cmd_set_threshold,
        "show-thresholds": _cmd_show_thresholds,
        "upsert-week": _cmd_upsert_week,
        "upsert-base-coverage": _cmd_upsert_base_coverage,
        "delete-week": _cmd_delete_week,
        "list-weeks": _cmd_list_weeks,
        "export-week": _cmd_export_week,
        "export-latest": _cmd_export_latest,
        "export-board-pack": _cmd_export_board_pack,
    }
    fn = commands[args.command]
    try:
        fn(args, db_path)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
