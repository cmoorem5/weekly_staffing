"""
CLI wrapper for database-backed quarterly staffing PDF reports.

For Django UI use Reports → Quarterly staffing report.
"""

from __future__ import annotations

import argparse
import sys

from staffing_tool.paths import OUTPUT_DIR
from staffing_tool.quarterly_pdf_report import (
    export_quarterly_staffing_pdf,
    list_fiscal_quarters,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build quarterly staffing PDF from staffing.db.",
    )
    parser.add_argument(
        "--fy",
        type=int,
        metavar="YYYY",
        help="Fiscal year label (e.g. 2026 for FY2026). Default: latest quarter in DB.",
    )
    parser.add_argument(
        "--quarter",
        type=int,
        choices=[1, 2, 3, 4],
        help="Fiscal quarter 1–4. Required with --fy unless using default.",
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

    fy = args.fy
    quarter = args.quarter
    if fy is None or quarter is None:
        quarters = list_fiscal_quarters(args.db)
        if not quarters:
            print("No quarterly data in database.", file=sys.stderr)
            sys.exit(1)
        if fy is None and quarter is None:
            latest = quarters[0]
            fy = latest["fy_label_year"]
            quarter = latest["quarter"]
        else:
            print(
                "Specify both --fy and --quarter, or omit both for latest.",
                file=sys.stderr,
            )
            sys.exit(1)

    out = args.output_dir or str(OUTPUT_DIR)
    path = export_quarterly_staffing_pdf(args.db, fy, quarter, out)
    print(f"Written: {path}")


if __name__ == "__main__":
    main()
