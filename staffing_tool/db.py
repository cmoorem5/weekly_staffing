"""
Database setup, session management, and seed data for staffing.db.
"""

import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

# Importing from .models loads models.py fully so all tables register on Base.
from .models import Base, BaseConfig, KpiThreshold, VehicleSlot


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_db_path(db_path: str | None) -> str:
    """Absolute, normalized path for stable engine cache keys."""
    path = db_path or "staffing.db"
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    return os.path.normpath(path)


@lru_cache(maxsize=32)
def _get_engine_cached(resolved_path: str) -> Engine:
    """One Engine per DB file — avoids new pools on every session_scope()."""
    # Windows: use forward slashes in URL
    url_path = resolved_path.replace(os.sep, "/")
    engine = create_engine(
        f"sqlite:///{url_path}",
        future=True,
        # Django / threaded apps may open sessions from different threads
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_enable_foreign_keys(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


@lru_cache(maxsize=32)
def _sessionmaker_for_path(resolved_path: str) -> sessionmaker:
    engine = _get_engine_cached(resolved_path)
    return sessionmaker(engine, expire_on_commit=False, autoflush=False)


def get_engine(db_path: str | None = None) -> Engine:
    """Return a cached SQLite engine. Default db_path is staffing.db (cwd)."""
    return _get_engine_cached(_resolve_db_path(db_path))


def create_tables(engine: Engine) -> None:
    """Create all tables."""
    Base.metadata.create_all(engine)


def _pragma_column_names(conn, table: str) -> set[str]:
    """Column names for a table (fixed table names only)."""
    r = conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in r}


def migrate_weekly_staffing_columns(engine: Engine) -> None:
    """Add missing optional columns on weekly_staffing (single PRAGMA pass)."""
    optional = (
        "ot_rn",
        "ot_medic",
        "ot_emt",
        "ot_rn_day",
        "ot_rn_night",
        "ot_medic_day",
        "ot_medic_night",
        "ot_emt_day",
        "ot_emt_night",
        "leave_jury",
        "leave_brev",
        "medic_unpartnered",
        "rn_unpartnered_staff",
    )
    alter_sql = (
        "ALTER TABLE weekly_staffing ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0"
    )
    with engine.connect() as conn:
        columns = _pragma_column_names(conn, "weekly_staffing")
        for col in optional:
            if col not in columns:
                conn.execute(text(alter_sql.format(col=col)))
        conn.commit()


def migrate_add_ot_columns(engine: Engine) -> None:
    """Backward-compatible alias: ensures OT and related weekly_staffing columns."""
    migrate_weekly_staffing_columns(engine)


def migrate_add_leave_jury(engine: Engine) -> None:
    """Backward-compatible alias."""
    migrate_weekly_staffing_columns(engine)


def migrate_add_leave_brev(engine: Engine) -> None:
    """Backward-compatible alias."""
    migrate_weekly_staffing_columns(engine)


def migrate_add_unpartnered_columns(engine: Engine) -> None:
    """Backward-compatible alias."""
    migrate_weekly_staffing_columns(engine)


def migrate_add_base_coverage_day_night(engine: Engine) -> None:
    """Add RW/GR day-night split columns to weekly_base_coverage if missing."""
    optional = (
        "rw_staffed_day",
        "rw_staffed_night",
        "gr_staffed_day",
        "gr_staffed_night",
    )
    alter_sql = (
        "ALTER TABLE weekly_base_coverage ADD COLUMN {col} "
        "INTEGER NOT NULL DEFAULT 0"
    )
    with engine.connect() as conn:
        columns = _pragma_column_names(conn, "weekly_base_coverage")
        for col in optional:
            if col not in columns:
                conn.execute(text(alter_sql.format(col=col)))
        conn.commit()


def migrate_system_gr_kpi_thresholds(engine: Engine) -> None:
    """
    System GR Coverage % RAG: green ≥92%, yellow 85%–<92%, red <85%
    (stored as fractions).

    Updates only rows that still match the previous shipped defaults
    (95% / 90%) so customized thresholds are left alone.
    """
    with engine.connect() as conn:
        r = conn.execute(
            text(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='kpi_thresholds'"
            )
        )
        if r.fetchone() is None:
            return
        conn.execute(
            text(
                """
                UPDATE kpi_thresholds SET
                    green_min = 0.92,
                    green_max = 1.0,
                    yellow_min = 0.85,
                    yellow_max = 0.919,
                    red_min = 0.0,
                    red_max = 0.849,
                    higher_is_better = 1
                WHERE metric_name = 'System GR Coverage %'
                  AND ABS(IFNULL(green_min, -1) - 0.95) < 0.0001
                  AND ABS(IFNULL(yellow_min, -1) - 0.90) < 0.0001
                """
            )
        )
        conn.commit()


# Default bases (Bedford, Lawrence, Mansfield, Manchester 12hr only, Plymouth)
# — RW and GR unit-days per week.
# GR: Bedford 14 (day+night); Lawrence/Mansfield/Plymouth 7 each;
# Manchester RW-only (0 GR).
DEFAULT_BASES = [
    ("Bedford", 14, 14),
    ("Lawrence", 14, 7),
    ("Mansfield", 7, 7),
    ("Manchester", 7, 0),  # 12hr only (RW)
    ("Plymouth", 14, 7),
]

# Default KPI thresholds:
# (metric_name, g_min, g_max, y_min, y_max, r_min, r_max, higher_is_better)
DEFAULT_THRESHOLDS = [
    ("Staffing Rate", 0.95, 1.0, 0.90, 0.949, 0.0, 0.90, 1),
    ("OT Dependency", 0.0, 0.08, 0.081, 0.12, 0.12, 1.0, 0),
    ("Leave Exposure", 0.0, 0.25, 0.251, 0.32, 0.32, 1.0, 0),
    (
        "Overnights Below Coverage",
        0.0,
        1.0,
        2.0,
        3.0,
        4.0,
        999.0,
        0,
    ),  # lower is better: green <=1, yellow 2-3, red >=4
    ("System RW Coverage %", 0.95, 1.0, 0.90, 0.949, 0.0, 0.90, 1),
    # Green ≥92%; yellow 85%–91.9%; red ≤84.9%
    ("System GR Coverage %", 0.92, 1.0, 0.85, 0.919, 0.0, 0.849, 1),
    # lower is better; green=0
    ("Pilot Vacancies", 0.0, 0.0, 1.0, 5.0, 6.0, 999.0, 0),
]

# CEO grid: vehicle_id, base_name, shift_label, vehicle_type, has_rn, has_medic,
# has_pilot, has_emt
DEFAULT_VEHICLE_SLOTS = [
    ("BR-D", "Bedford", "7a-7p", "RW", 1, 1, 1, 0),
    ("BG-D", "Bedford", "7a-7p", "GR", 1, 1, 0, 1),
    ("BR-N", "Bedford", "7p-7a", "RW", 1, 1, 1, 0),
    ("BG-N", "Bedford", "7p-7a", "GR", 1, 1, 0, 1),
    ("PR-D", "Plymouth", "7a-7p", "RW", 1, 1, 1, 1),
    ("PR-N", "Plymouth", "7p-7a", "RW", 1, 1, 1, 1),
    ("LR-D", "Lawrence", "9a-9p", "RW", 1, 1, 1, 0),
    ("LG-D", "Lawrence", "9a-9p", "GR", 1, 1, 0, 1),
    ("LR-N", "Lawrence", "9p-9a", "combined", 1, 1, 1, 1),
    ("MG-D", "Mansfield", "11a-11p", "GR", 1, 1, 0, 1),
    ("MR-D", "Mansfield", "11a-11p", "RW", 1, 1, 1, 0),
    ("MH-D", "Manchester", "11a-11p", "RW", 1, 1, 1, 0),
]


def seed_vehicle_slots(session: Session) -> None:
    """Insert default vehicle_slots (CEO position grid rows) if not present."""
    existing = {r.vehicle_id for r in session.query(VehicleSlot.vehicle_id).all()}
    for row in DEFAULT_VEHICLE_SLOTS:
        vid, base, shift, vtype, rn, medic, pilot, emt = row
        if vid not in existing:
            session.add(
                VehicleSlot(
                    vehicle_id=vid,
                    base_name=base,
                    shift_label=shift,
                    vehicle_type=vtype,
                    has_rn=rn,
                    has_medic=medic,
                    has_pilot=pilot,
                    has_emt=emt,
                )
            )


def seed_base_config(session: Session) -> None:
    """Insert default base_config rows if not present."""
    existing = {r.base_name for r in session.query(BaseConfig.base_name).all()}
    now = _utc_now_iso()
    for base_name, rw_total, gr_total in DEFAULT_BASES:
        if base_name not in existing:
            session.add(
                BaseConfig(
                    base_name=base_name,
                    rw_total_unit_days=rw_total,
                    gr_total_unit_days=gr_total,
                    updated_at=now,
                )
            )


def seed_kpi_thresholds(session: Session) -> None:
    """Insert default kpi_thresholds if not present."""
    existing = {
        r.metric_name for r in session.query(KpiThreshold.metric_name).all()
    }
    for row in DEFAULT_THRESHOLDS:
        name, g_min, g_max, y_min, y_max, r_min, r_max, higher = row
        if name not in existing:
            session.add(
                KpiThreshold(
                    metric_name=name,
                    green_min=g_min,
                    green_max=g_max,
                    yellow_min=y_min,
                    yellow_max=y_max,
                    red_min=r_min,
                    red_max=r_max,
                    higher_is_better=higher,
                )
            )


def init_db(db_path: str | None = None) -> None:
    """Create tables, run migrations, and seed base_config + kpi_thresholds."""
    engine = get_engine(db_path)
    create_tables(engine)
    migrate_weekly_staffing_columns(engine)
    migrate_add_base_coverage_day_night(engine)
    SessionLocal = _sessionmaker_for_path(_resolve_db_path(db_path))
    with SessionLocal() as session:
        seed_base_config(session)
        seed_kpi_thresholds(session)
        seed_vehicle_slots(session)
        session.commit()
    migrate_system_gr_kpi_thresholds(engine)


@contextmanager
def session_scope(db_path: str | None = None) -> Generator[Session, None, None]:
    """Context manager for a single database session."""
    resolved = _resolve_db_path(db_path)
    SessionLocal = _sessionmaker_for_path(resolved)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
