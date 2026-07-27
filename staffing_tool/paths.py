"""Repository paths shared by report builders and the Django app."""

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
FONT_DIR = PROJECT_ROOT / "fonts"
OUTPUT_DIR = PROJECT_ROOT / "output"
ASSETS_DIR = PROJECT_ROOT / "assets"

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
