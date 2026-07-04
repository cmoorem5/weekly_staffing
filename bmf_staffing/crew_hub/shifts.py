"""
Single source of truth for the Crew Hub / AOC Daily Report shift skeleton.

Bases, crew shifts (with role composition), Comm Center seats, duty officer
roles, the vehicle fleet, and default system-miss categories all live here so
models, seeding, forms, and the email template never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Bases -------------------------------------------------------------

BASE_BED = "BED"
BASE_PYM = "PYM"
BASE_LWM = "LWM"
BASE_MAN = "MAN"
BASE_MHT = "MHT"

BASE_CHOICES = [
    (BASE_BED, "Bedford"),
    (BASE_PYM, "Plymouth"),
    (BASE_LWM, "Lawrence"),
    (BASE_MAN, "Mansfield"),
    (BASE_MHT, "Manchester"),
]
BASE_LABELS = dict(BASE_CHOICES)

# Display headers used by the reference form/email, e.g. "Bedford (BED)".
BASE_HEADERS = {code: f"{label} ({code})" for code, label in BASE_CHOICES}

# Order used by the Transports "Completed by Base" table in the email.
TRANSPORT_BASE_ORDER = [BASE_BED, BASE_LWM, BASE_MHT, BASE_MAN, BASE_PYM]

# --- Crew positions ----------------------------------------------------

POSITION_RN = "RN"
POSITION_EMTP = "EMTP"
POSITION_PILOT = "PILOT"
POSITION_EMT = "EMT"

POSITION_CHOICES = [
    (POSITION_RN, "RN"),
    (POSITION_EMTP, "EMTP"),
    (POSITION_PILOT, "Pilot"),
    (POSITION_EMT, "EMT"),
]
POSITION_LABELS = dict(POSITION_CHOICES)

# Role composition rules.
ROTOR_POSITIONS = (POSITION_RN, POSITION_EMTP, POSITION_PILOT)
GROUND_CC_POSITIONS = (POSITION_RN, POSITION_EMTP, POSITION_EMT)
EMT_ONLY_POSITIONS = (POSITION_EMT,)


@dataclass(frozen=True)
class CrewShift:
    base: str
    code: str
    label: str  # As shown on the form/email, e.g. "PG EMT".
    time: str  # e.g. "0700–1900"; empty for untimed rows.
    positions: tuple[str, ...]


# The locked shift skeleton. PG carries one mandatory EMT; any bonus RN/EMTP
# on PG is logged only through Extras, never as a PG crew row.
CREW_SHIFTS: tuple[CrewShift, ...] = (
    # Bedford: D7B, GR, N7B, NG
    CrewShift(BASE_BED, "D7B", "D7B", "0700–1900", ROTOR_POSITIONS),
    CrewShift(BASE_BED, "GR", "GR", "0700–1900", GROUND_CC_POSITIONS),
    CrewShift(BASE_BED, "N7B", "N7B", "1900–0700", ROTOR_POSITIONS),
    CrewShift(BASE_BED, "NG", "NG", "1900–0700", GROUND_CC_POSITIONS),
    # Plymouth: D7P, PG, N7P, NP
    CrewShift(BASE_PYM, "D7P", "D7P", "0700–1900", ROTOR_POSITIONS),
    CrewShift(BASE_PYM, "PG", "PG EMT", "0700–1900", EMT_ONLY_POSITIONS),
    CrewShift(BASE_PYM, "N7P", "N7P", "1900–0700", ROTOR_POSITIONS),
    CrewShift(BASE_PYM, "NP", "NP EMT", "1900–0700", EMT_ONLY_POSITIONS),
    # Lawrence: D9L, LG, N9L, NL
    CrewShift(BASE_LWM, "D9L", "D9L", "0900–2100", ROTOR_POSITIONS),
    CrewShift(BASE_LWM, "LG", "LG", "0900–2100", GROUND_CC_POSITIONS),
    CrewShift(BASE_LWM, "N9L", "N9L", "2100–0900", ROTOR_POSITIONS),
    CrewShift(BASE_LWM, "NL", "NL EMT", "2100–0900", EMT_ONLY_POSITIONS),
    # Mansfield: D11M, MG (both 1100–2300)
    CrewShift(BASE_MAN, "D11M", "D11M", "1100–2300", ROTOR_POSITIONS),
    CrewShift(BASE_MAN, "MG", "MG", "1100–2300", GROUND_CC_POSITIONS),
    # Manchester: D11H (day only)
    CrewShift(BASE_MHT, "D11H", "D11H", "1100–2300", ROTOR_POSITIONS),
)

SHIFT_CHOICES = sorted({(s.code, s.code) for s in CREW_SHIFTS})

# (base, shift_code) -> CrewShift
CREW_SHIFT_INDEX = {(s.base, s.code): s for s in CREW_SHIFTS}

# Bases in form order with their shifts, for building UI sections.
BASE_ORDER = [BASE_BED, BASE_PYM, BASE_LWM, BASE_MAN, BASE_MHT]
SHIFTS_BY_BASE = {
    base: [s for s in CREW_SHIFTS if s.base == base] for base in BASE_ORDER
}

EXPECTED_CREW_ROW_COUNT = sum(len(s.positions) for s in CREW_SHIFTS)


def is_valid_crew_combo(base: str, shift_code: str, position: str) -> bool:
    """True when the base/shift/position triple exists in the skeleton."""
    shift = CREW_SHIFT_INDEX.get((base, shift_code))
    return shift is not None and position in shift.positions


# --- Duty officers -----------------------------------------------------

DUTY_ROLE_CHOICES = [
    ("AOC", "AOC"),
    ("AAOC", "AAOC"),
    ("MDOC", "MDOC"),
    ("PEDIDOC", "PediDOC"),
    ("ITOC", "ITOC"),
    ("BPM", "BPM"),
]
DUTY_ROLE_LABELS = dict(DUTY_ROLE_CHOICES)
DUTY_ROLE_ORDER = [code for code, _ in DUTY_ROLE_CHOICES]


# --- Comm Center seats -------------------------------------------------


@dataclass(frozen=True)
class CommSeat:
    code: str
    label: str
    time: str
    # Paid hours for one shift in this seat (payroll/ADP export).
    hours: float = 12.0


COMM_SEATS: tuple[CommSeat, ...] = (
    CommSeat("D", "D", "0630–1830"),
    CommSeat("D2", "D-2", "0630–1830"),
    CommSeat("S", "S", "0730–1930"),
    CommSeat("S2", "S-2", "0730–1930"),
    CommSeat("S3", "S-3", "0730–1930"),
    CommSeat("N", "N", "1830–0630"),
    CommSeat("N2", "N-2", "1830–0630"),
    CommSeat("P", "P", "1930–0730"),
    CommSeat("P2", "P-2", "1930–0730"),
    # Orientee/extra time varies; counted as 0 hours unless payroll says
    # otherwise — adjust in the exported CSV if needed.
    CommSeat("EXTRA", "Orientee / Extra", "", hours=0.0),
)

COMM_SEAT_CHOICES = [(s.code, s.label) for s in COMM_SEATS]
COMM_SEAT_INDEX = {s.code: s for s in COMM_SEATS}
# Two-column layout used by the reference form/email: day-side, night-side.
COMM_SEAT_COLUMNS = (["D", "D2", "S", "S2", "S3"], ["N", "N2", "P", "P2", "EXTRA"])


# --- Vehicle fleet -----------------------------------------------------

VEHICLE_CATEGORY_RW = "RW"
VEHICLE_CATEGORY_GR = "GR"

VEHICLE_CATEGORY_CHOICES = [
    (VEHICLE_CATEGORY_RW, "Rotor Wing"),
    (VEHICLE_CATEGORY_GR, "Ground Trucks"),
]

# (identifier, category) in board order, mirroring the reference form.
FLEET: tuple[tuple[str, str], ...] = (
    ("N141NE", VEHICLE_CATEGORY_RW),
    ("N142NE", VEHICLE_CATEGORY_RW),
    ("N143NE", VEHICLE_CATEGORY_RW),
    ("N144NE", VEHICLE_CATEGORY_RW),
    ("N145NE", VEHICLE_CATEGORY_RW),
    ("N246NE", VEHICLE_CATEGORY_RW),
    ("N247NE", VEHICLE_CATEGORY_RW),
    ("Med 11", VEHICLE_CATEGORY_GR),
    ("Med 12", VEHICLE_CATEGORY_GR),
    ("Med 14", VEHICLE_CATEGORY_GR),
    ("Med 15", VEHICLE_CATEGORY_GR),
    ("Med 16", VEHICLE_CATEGORY_GR),
    ("Med 17", VEHICLE_CATEGORY_GR),
    ("Med 18", VEHICLE_CATEGORY_GR),
    ("Med 19", VEHICLE_CATEGORY_GR),
    ("Med 20", VEHICLE_CATEGORY_GR),
)


# --- Transports --------------------------------------------------------

# Default system-miss categories; labels stay editable per report.
DEFAULT_MISS_CATEGORIES = [
    "Cancelled",
    "Sending canceled",
    "ETA unacceptable",
    "Inquiry only",
    "Patient expired",
    "Weather",
]
