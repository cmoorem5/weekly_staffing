"""
CLI wrapper for database-backed weekly staffing PDF + HTML reports.

For Django UI use Reports → Weekly staffing report.
"""

from __future__ import annotations

import argparse
import sys

from staffing_tool.paths import OUTPUT_DIR
from staffing_tool.weekly_pdf_report import (
    export_weekly_staffing_both,
    list_week_starts,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build weekly staffing PDF + HTML from staffing.db.",
    )
    parser.add_argument(
        "--week",
        metavar="YYYY-MM-DD",
        help="Week start (Sunday). Default: latest week in the database.",
    )
    parser.add_argument(
        "--db",
        default="staffing.db",
        help="Path to staffing.db (default: staffing.db in cwd).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=f"Output directory (default: {OUTPUT_DIR}).",
    )
    args = parser.parse_args(argv)

    week = args.week
    if not week:
        weeks = list_week_starts(args.db)
        if not weeks:
            print(
                "No weeks in database. Import a schedule or add a week first.",
                file=sys.stderr,
            )
            sys.exit(1)
        week = weeks[0]

    out = args.output_dir or str(OUTPUT_DIR)
    pdf_path, html_path = export_weekly_staffing_both(args.db, week, out)
    print(f"Written: {pdf_path}")
    print(f"Written: {html_path}")


if __name__ == "__main__":
    main()
