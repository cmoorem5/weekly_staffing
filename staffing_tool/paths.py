"""Repository paths shared by report builders and the Django app."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
FONT_DIR = PROJECT_ROOT / "fonts"
OUTPUT_DIR = PROJECT_ROOT / "output"
