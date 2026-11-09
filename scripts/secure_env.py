#!/usr/bin/env python3
"""Make the .env on a desktop install safe to run from: real secret key, DEBUG off.

Update_Crew_Hub.bat creates .env by copying .env.example, so every install
started out with the sample secret key (public, it is in this repository) and
DJANGO_DEBUG=true (debug error pages). This fixes both in place and is safe to
rerun:

- DJANGO_SECRET_KEY: replaced with a random value when it is missing, empty or
  a known placeholder. A key someone set is never touched. Replacing it signs
  everyone out once.
- DJANGO_DEBUG=false plus DJANGO_LOOPBACK_HTTP=true (see settings.py): applied
  once, the first time, and only when the allowed hosts are loopback. After
  that the file is yours: a .env that already has a DJANGO_LOOPBACK_HTTP line
  (either value) is left alone, so setting DJANGO_DEBUG=true to debug sticks.

Every other line, comments and line endings included, is kept as it is.

    python scripts/secure_env.py            # the .env next to this repo
    python scripts/secure_env.py --dry-run
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Keep in step with PLACEHOLDER_SECRET_KEYS in bmf_staffing/settings.py.
PLACEHOLDER_SECRET_KEYS = (
    "",
    "dev-key-change-in-production",
    "change-me-to-a-long-random-string",
)
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _value(line: str) -> str:
    return line.split("=", 1)[1].strip().strip("\"'")


def _find(lines: list[str], name: str) -> int | None:
    """Index of the last uncommented ``NAME=`` line (the one dotenv uses)."""
    found = None
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{name}="):
            found = i
    return found


def _set(lines: list[str], name: str, value: str) -> None:
    i = _find(lines, name)
    if i is None:
        lines.append(f"{name}={value}")
    else:
        lines[i] = f"{name}={value}"


def harden(text: str) -> tuple[str, list[str]]:
    """Return the hardened file text and a plain list of what changed."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    changes: list[str] = []

    i = _find(lines, "DJANGO_SECRET_KEY")
    if i is None or _value(lines[i]) in PLACEHOLDER_SECRET_KEYS:
        _set(lines, "DJANGO_SECRET_KEY", secrets.token_urlsafe(50))
        changes.append(
            "Replaced the sample secret key with a random one "
            "(everyone signs in again once)."
        )

    if _find(lines, "DJANGO_LOOPBACK_HTTP") is None:
        i = _find(lines, "DJANGO_ALLOWED_HOSTS")
        hosts = (
            {h.strip() for h in _value(lines[i]).split(",") if h.strip()}
            if i is not None
            else set()
        )
        if hosts <= LOOPBACK_HOSTS:
            _set(lines, "DJANGO_DEBUG", "false")
            _set(lines, "DJANGO_LOOPBACK_HTTP", "true")
            changes.append(
                "Turned debug mode off for this desktop install "
                "(DJANGO_DEBUG=false, DJANGO_LOOPBACK_HTTP=true)."
            )
        else:
            changes.append(
                "Left DJANGO_DEBUG alone: DJANGO_ALLOWED_HOSTS names other "
                "machines, so this is not a desktop-only install."
            )
    return newline.join(lines) + newline, changes


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--env", default=str(PROJECT_ROOT / ".env"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    path = Path(args.env)
    if not path.is_file():
        print(f"      No settings file at {path}; nothing to do.")
        return 0
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new_text, changes = harden(text)
    for change in changes:
        print(f"      {change}")
    if new_text != text and not args.dry_run:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
