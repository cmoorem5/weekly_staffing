"""
SQLAlchemy ORM models for staffing.db (system of record).
"""

from sqlalchemy import Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class BaseConfig(Base):
    """Per-base RW/GR total unit-days per week (denominators)."""

    __tablename__ = "base_config"

    base_name = Column(String(64), primary_key=True)
    rw_total_unit_days = Column(Integer, nullable=False, default=0)
    gr_total_unit_days = Column(Integer, nullable=False, default=0)
    updated_at = Column(String(32), nullable=True)

    def __repr__(self) -> str:
        return f"BaseConfig(base_name={self.base_name!r}, rw={self.rw_total_unit_days}, gr={self.gr_total_unit_days})"


class KpiThreshold(Base):
    """Effective thresholds for RAG evaluation. Supports higher-is-better and lower-is-better."""

    __tablename__ = "kpi_thresholds"

    metric_name = Column(String(128), primary_key=True)
    green_min = Column(Float, nullable=True)
    green_max = Column(Float, nullable=True)
    yellow_min = Column(Float, nullable=True)
    yellow_max = Column(Float, nullable=True)
    red_min = Column(Float, nullable=True)
    red_max = Column(Float, nullable=True)
    higher_is_better = Column(
        Integer, nullable=False, default=1
    )  # 1 = higher is better, 0 = lower

    def __repr__(self) -> str:
        return f"KpiThreshold(metric_name={self.metric_name!r})"


class ManagerRosterLastName(Base):
    """Last names that identify manager rows on the schedule (cols A–B tokens)."""

    __tablename__ = "manager_roster_last_name"

    id = Column(Integer, primary_key=True, autoincrement=True)
    last_name = Column(String(128), nullable=False, unique=True)

    def __repr__(self) -> str:
        return f"ManagerRosterLastName(last_name={self.last_name!r})"


class WeeklyManagerShift(Base):
    """One staffed line shift worked by a manager (from schedule import)."""

    __tablename__ = "weekly_manager_shifts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_start = Column(
        String(10),
        ForeignKey("weekly_staffing.week_start", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_display = Column(String(256), nullable=False, default="")
    role = Column(String(16), nullable=False)
    shift_date = Column(String(10), nullable=False)
    base_name = Column(String(64), nullable=False)
    service_type = Column(String(8), nullable=False)
    day_night = Column(String(1), nullable=False)
    unit_code = Column(String(32), nullable=False, default="")
    overtime = Column(Integer, nullable=False, default=0)
    raw_value = Column(String(64), nullable=False, default="")
    source_tab = Column(String(128), nullable=False, default="")
    source_cell = Column(String(16), nullable=False, default="")


class WeeklyStaffing(Base):
    """Weekly staffing record (one row per week)."""

    __tablename__ = "weekly_staffing"

    # week_start represents the first day of the week (now Sunday, YYYY-MM-DD).
    week_start = Column(String(10), primary_key=True)
    day_target = Column(Integer, nullable=False, default=8)
    night_min = Column(Integer, nullable=False, default=4)
    filled_day = Column(Integer, nullable=False)
    filled_night = Column(Integer, nullable=False)

    # OT is tracked separately by role; ot_shifts stores the total for compatibility.
    ot_shifts = Column(Integer, nullable=False, default=0)
    ot_rn = Column(Integer, nullable=False, default=0)
    ot_medic = Column(Integer, nullable=False, default=0)
    ot_emt = Column(Integer, nullable=False, default=0)
    # Day / Night split by role (shift counts, not hours).
    ot_rn_day = Column(Integer, nullable=False, default=0)
    ot_rn_night = Column(Integer, nullable=False, default=0)
    ot_medic_day = Column(Integer, nullable=False, default=0)
    ot_medic_night = Column(Integer, nullable=False, default=0)
    ot_emt_day = Column(Integer, nullable=False, default=0)
    ot_emt_night = Column(Integer, nullable=False, default=0)

    leave_at = Column(Integer, nullable=False, default=0)
    leave_lt = Column(Integer, nullable=False, default=0)
    leave_sick = Column(Integer, nullable=False, default=0)
    leave_loa = Column(Integer, nullable=False, default=0)
    leave_jury = Column(Integer, nullable=False, default=0)
    leave_brev = Column(Integer, nullable=False, default=0)
    # leave_pfml is retained for legacy data but treated as part of LOA in practice.
    leave_pfml = Column(Integer, nullable=False, default=0)

    overnights_below = Column(Integer, nullable=False, default=0)
    pilot_vacancies = Column(Integer, nullable=False, default=0)
    # CEO report: manually entered unpartnered staff counts (not derived from schedule import).
    medic_unpartnered = Column(Integer, nullable=False, default=0)
    rn_unpartnered_staff = Column(Integer, nullable=False, default=0)
    unpartnered_note_medic = Column(String(200), nullable=True)
    unpartnered_note_rn = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    entered_by = Column(String(128), nullable=True)
    created_at = Column(String(32), nullable=True)
    updated_at = Column(String(32), nullable=True)

    base_coverages = relationship(
        "WeeklyBaseCoverage", back_populates="week", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"WeeklyStaffing(week_start={self.week_start!r}, filled_day={self.filled_day}, filled_night={self.filled_night})"


class WeeklyBaseCoverage(Base):
    """Per-week, per-base staffed RW/GR unit-days."""

    __tablename__ = "weekly_base_coverage"

    week_start = Column(
        String(10),
        ForeignKey("weekly_staffing.week_start", ondelete="CASCADE"),
        primary_key=True,
    )
    base_name = Column(
        String(64), ForeignKey("base_config.base_name"), primary_key=True
    )
    rw_staffed_unit_days = Column(Integer, nullable=False, default=0)
    gr_staffed_unit_days = Column(Integer, nullable=False, default=0)
    # Day/night split (optional; when all zero, report uses totals as RW/D and GR/D only)
    rw_staffed_day = Column(Integer, nullable=False, default=0)
    rw_staffed_night = Column(Integer, nullable=False, default=0)
    gr_staffed_day = Column(Integer, nullable=False, default=0)
    gr_staffed_night = Column(Integer, nullable=False, default=0)

    week = relationship("WeeklyStaffing", back_populates="base_coverages")

    def __repr__(self) -> str:
        return f"WeeklyBaseCoverage(week_start={self.week_start!r}, base={self.base_name!r}, rw={self.rw_staffed_unit_days}, gr={self.gr_staffed_unit_days})"


class WeeklyLeaveDetail(Base):
    """Per-week, per-role, per-leave-type: count for absence grid (columns = leave types, rows = RN, Medic, EMT, Pilot)."""

    __tablename__ = "weekly_leave_detail"

    week_start = Column(
        String(10),
        ForeignKey("weekly_staffing.week_start", ondelete="CASCADE"),
        primary_key=True,
    )
    role = Column(String(16), primary_key=True)  # RN, Medic, EMT, Pilot
    leave_type = Column(
        String(16), primary_key=True
    )  # AT, LT-D, LT-N, SICK, LOA, JURY, etc.
    count = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"WeeklyLeaveDetail(week={self.week_start!r}, role={self.role!r}, {self.leave_type!r}={self.count})"


class VehicleSlot(Base):
    """One row in the CEO position grid: vehicle/shift and which positions (RN, Medic, Pilot, EMT) it has."""

    __tablename__ = "vehicle_slots"

    vehicle_id = Column(String(32), primary_key=True)  # e.g. BR-D, BG-N
    base_name = Column(String(64), nullable=False)
    shift_label = Column(String(32), nullable=False)  # e.g. 7a-7p, 7p-7a
    vehicle_type = Column(String(16), nullable=False)  # RW, GR, or combined
    has_rn = Column(Integer, nullable=False, default=1)
    has_medic = Column(Integer, nullable=False, default=1)
    has_pilot = Column(Integer, nullable=False, default=0)
    has_emt = Column(Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"VehicleSlot(vehicle_id={self.vehicle_id!r}, base={self.base_name!r})"


class WeeklyStaffingDetail(Base):
    """Per-week, per-vehicle, per-position: cell value = '1' (filled) or reason (sick, LOA, AT, LT, vacant, etc.)."""

    __tablename__ = "weekly_staffing_detail"

    week_start = Column(
        String(10),
        ForeignKey("weekly_staffing.week_start", ondelete="CASCADE"),
        primary_key=True,
    )
    vehicle_id = Column(
        String(32), ForeignKey("vehicle_slots.vehicle_id"), primary_key=True
    )
    position = Column(String(16), primary_key=True)  # RN, Medic, Pilot, EMT
    value = Column(
        String(32), nullable=False, default="1"
    )  # "1" or "sick", "LOA", "AT", "LT", "vacant", etc.

    def __repr__(self) -> str:
        return f"WeeklyStaffingDetail(week={self.week_start!r}, vehicle={self.vehicle_id!r}, {self.position}={self.value!r})"
