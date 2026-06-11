"""Download Barlow + IBM Plex Mono TTF files into ./fonts/ for report builds."""

from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / "fonts"

# Google Fonts raw paths (stable CDN)
FONTS = {
    "Barlow-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/barlow/Barlow-Regular.ttf",
    "Barlow-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/barlow/Barlow-Bold.ttf",
    "Barlow-SemiBold.ttf": "https://github.com/google/fonts/raw/main/ofl/barlow/Barlow-SemiBold.ttf",
    "BarlowCondensed-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/barlowcondensed/BarlowCondensed-Bold.ttf",
    "IBMPlexMono-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/ibmplexmono/IBMPlexMono-Regular.ttf",
    "IBMPlexMono-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/ibmplexmono/IBMPlexMono-Bold.ttf",
}


def main() -> None:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, url in FONTS.items():
        dest = FONT_DIR / filename
        if dest.exists():
            print(f"skip  {filename}")
            continue
        print(f"fetch {filename}")
        urllib.request.urlretrieve(url, dest)
    print(f"Fonts ready in {FONT_DIR}")


if __name__ == "__main__":
    main()
