"""Build BMF_Staffing.ico from the coastal logo PNG (multi-size for Windows)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG = (
    ROOT
    / "bmf_staffing"
    / "dashboard"
    / "static"
    / "dashboard"
    / "images"
    / "BMF_Coastal_Logos.png"
)
ICO = ROOT / "assets" / "BMF_Staffing.ico"


def main() -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is required: pip install Pillow", file=sys.stderr)
        return 1

    if not PNG.is_file():
        print(f"Logo not found: {PNG}", file=sys.stderr)
        return 1

    ICO.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(PNG).convert("RGBA")
    sizes = [(256, 256), (128, 128), 64, 48, 32, 16]
    img.save(
        ICO, format="ICO", sizes=[(s, s) if isinstance(s, int) else s for s in sizes]
    )
    print(f"Wrote {ICO}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
