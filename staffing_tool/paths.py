"""Repository paths shared by report builders and the Django app."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
FONT_DIR = PROJECT_ROOT / "fonts"
OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"
#: Backups always live in the repo folder, wherever the live databases are:
#: on the desktop install this folder syncs to OneDrive, which is the
#: off-machine copy.
ARCHIVE_DIR = PROJECT_ROOT / "archive"

#: Optional folder holding the two live SQLite databases. Unset, they sit in
#: the repo (staffing.db at the root, bmf_staffing/db.sqlite3). The desktop
#: install sets it to a folder outside the repo, because the repo folder syncs
#: through OneDrive and a sync client copying a database mid-write is how
#: SQLite files get corrupted. scripts/relocate_databases.py sets it up.
DATA_DIR_ENV = "BMF_STAFFING_DATA_DIR"
STAFFING_DB_NAME = "staffing.db"
CREW_HUB_DB_NAME = "db.sqlite3"

#: Boston MedFlight coastal logo, in preference order. Override with the
#: WEEKLY_STAFFING_LOGO environment variable (a file path).
LOGO_CANDIDATES = (
    ASSETS_DIR / "bmf_coastal_logo.png",
    PROJECT_ROOT
    / "bmf_staffing"
    / "dashboard"
    / "static"
    / "dashboard"
    / "images"
    / "BMF_Coastal_Logos.png",
)


def resolve_logo_path() -> Path | None:
    """First existing BMF logo file, or None when the asset is missing.

    Every report builder (Excel, HTML/email, dashboard) resolves the logo
    through here so a WEEKLY_STAFFING_LOGO override applies everywhere.
    """
    env = os.environ.get("WEEKLY_STAFFING_LOGO", "").strip()
    if env and os.path.isfile(env):
        return Path(env)
    for candidate in LOGO_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def _load_dotenv() -> None:
    """Read the repo-root .env so scripts see the same settings Django does."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # optional: plain environment variables still work
        return
    load_dotenv(PROJECT_ROOT / ".env")


def expand_data_dir(raw: str) -> Path:
    """Turn a ``BMF_STAFFING_DATA_DIR`` value into a path.

    The value may use environment variables (``%LOCALAPPDATA%\\...``), so one
    .env works for any Windows user. A variable that does not resolve is an
    error, never a quiet fallback: falling back would open a new, empty
    database and look exactly like lost data.
    """
    expanded = os.path.expanduser(os.path.expandvars(raw.strip()))
    if "%" in expanded or "$" in expanded:
        raise RuntimeError(
            f"{DATA_DIR_ENV}={raw!r} uses a variable that is not set on this "
            "machine. Fix it in .env (or remove the line to keep the databases "
            "in the program folder)."
        )
    return Path(expanded)


def data_dir() -> Path | None:
    """The configured database folder, or None when the databases are in the repo."""
    _load_dotenv()
    raw = os.environ.get(DATA_DIR_ENV, "").strip()
    return expand_data_dir(raw) if raw else None


def staffing_db_path() -> Path:
    """Where staffing.db lives. Every script and the dashboard resolve it here."""
    base = data_dir()
    return (base or PROJECT_ROOT) / STAFFING_DB_NAME


def crew_hub_db_path() -> Path:
    """Where the Django (Crew Hub) SQLite database lives."""
    base = data_dir()
    return (base or PROJECT_ROOT / "bmf_staffing") / CREW_HUB_DB_NAME


def default_cli_db_path() -> str:
    """Default ``--db`` for the CLI and standalone report builders.

    Historically ``staffing.db`` in the current directory, and still that when
    the databases live in the repo. Once they have been moved, the relative
    name would quietly create a new empty file, so the live path is used.
    """
    return str(staffing_db_path()) if data_dir() is not None else STAFFING_DB_NAME


def _main(argv: list[str]) -> int:
    """``python -m staffing_tool.paths staffing|crewhub``: print a database path.

    For the .bat scripts, which cannot read .env themselves.
    """
    which = argv[1] if len(argv) > 1 else ""
    if which == "staffing":
        print(staffing_db_path())
    elif which == "crewhub":
        print(crew_hub_db_path())
    else:
        print("usage: python -m staffing_tool.paths staffing|crewhub")
        return 2
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_main(sys.argv))
