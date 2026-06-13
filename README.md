# Weekly staffing (BMF)

Board-level staffing KPI tooling: a **`staffing_tool`** Python package (CLI + Excel reports) and an optional **Django dashboard** under **`bmf_staffing/`**. Data is stored in a local SQLite file **`staffing.db`** next to this README.

**Python:** 3.12+ (Django 6.0)

## Repository layout

| Path | Purpose |
|------|--------|
| `staffing_tool/` | Core library: DB models, metrics, Excel export, schedule import, PDF report builders (`report_style`, `weekly_pdf_report`, `quarterly_pdf_report`) |
| `bmf_staffing/` | Django project: web UI for weeks, base totals, schedule import, monthly report |
| `scripts/` | CLI helpers: `build_weekly_report.py`, `build_quarterly_report.py`, `backfill_schedules.py`, `setup_report_fonts.py` |
| `docs/` | Report style spec and generator notes |
| `fonts/` | Barlow + IBM Plex Mono (run `python scripts/setup_report_fonts.py` once) |
| `requirements.txt` | Runtime dependencies (SQLAlchemy, openpyxl, Pillow, Django) |
| `requirements-dev.txt` | Adds Ruff for lint/format |
| `output/` | Generated exports — Excel, PDF, HTML (gitignored) |
| `uploads/` | Temporary schedule uploads from the dashboard (gitignored) |

## Install

From this directory:

```bash
pip install -r requirements.txt
```

For development (lint/format):

```bash
pip install -r requirements-dev.txt
```

## Configuration (environment variables)

The dashboard runs as a localhost tool out of the box. For any networked
deployment, set these before starting Django:

| Variable | Default | Notes |
|----------|---------|-------|
| `DJANGO_SECRET_KEY` | dev placeholder | **Required** when `DJANGO_DEBUG` is off; startup fails otherwise. Use a long random value. |
| `DJANGO_DEBUG` | `true` | Set to `0`/`false` in production. Turning this off automatically enables secure cookies, HSTS, content-type nosniff, `X-Frame-Options: DENY`, and HTTPS redirect. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames. |
| `DJANGO_SSL_REDIRECT` | `1` | Set to `0` if TLS is terminated elsewhere and you don't want Django to force redirects. |

Validate a production configuration with `python manage.py check --deploy`.

## Command line

Initialize or migrate the database, then use subcommands such as `upsert-week`, `export-board-pack`:

```bash
python -m staffing_tool init-db
python -m staffing_tool --help
```

### Weekly / quarterly PDF reports

From the repo root (after `pip install -r requirements.txt` and `python scripts/setup_report_fonts.py`):

```bash
# Weekly PDF + HTML from staffing.db (latest week if --week omitted)
python scripts/build_weekly_report.py --week 2026-05-24
python scripts/build_weekly_report.py

# Quarterly PDF from staffing.db (latest quarter if --fy/--quarter omitted)
python scripts/build_quarterly_report.py --fy 2026 --quarter 2
python scripts/build_quarterly_report.py
```

In the Django dashboard: **Reports → Weekly staffing report** or **Quarterly staffing report**. After importing a schedule, the week edit page offers one-click PDF/HTML download.

**Staff roster** (RN / Medic / EMT): **Settings → Staff roster** (`/settings/staff-roster/`). The roster **updates automatically on schedule import** — new clinical names are added and `staff_member_id` links are set in the same pass. Deactivate people here if they should not appear in the Staff ops report; deactivated entries are not re-added on import. Manual backfill from an older week is optional on that page.

**Staff ops report** (ops-only, not CEO aggregate): **Reports → Staff ops report** (`/ops/person/`). Pick a person and date range to see RW/GR mix, staffed shifts, leave/exceptions, and OT (skipped training/admin cells are stored but hidden from this view). CEO aggregate reports are unchanged.

## Data model / future reports

Schedule imports persist **summary tables** (CEO weekly/quarterly inputs) plus **full detail** for new reports:

| Table | Contents |
|-------|----------|
| `weekly_staffing`, `weekly_base_coverage`, `weekly_daily_detail`, `weekly_leave_detail` | CEO report aggregates (derived at import) |
| `schedule_imports` | One audit row per week: filename, path, SHA-256, `parser_version`, row counts |
| `weekly_person_shifts` | Every person cell: staffed, leave, OT, and **skipped** (training/open/admin) with `skip_reason`, Excel row/col, optional roster link |
| `weekly_manager_shifts` | Manager line shifts and **AOC days** (`event_type`: `line_shift` or `aoc`; AOC excluded from CEO clinical totals) |
| `weekly_ops_view_days` | OPS View staffed RW/GR unit-days per calendar day and base |
| `weekly_ops_view_assignments` | OPS View name-level cells (unit, role, person text) |
| `schedule_parse_issues` | Unknown units and other parser issues |
| `unit_code_mappings` | Persisted raw unit → canonical mappings from dashboard Apply (used by bulk backfill) |
| `schedule_raw_cells` | Optional grid archive (~500–2000 cells/week); skipped if over 5000 cells |

Query helpers: `staffing_tool/schedule_data.py` (`get_week_person_events`, `get_week_ops_view_days`, `get_week_all_cells`, `list_imports_for_week`).

**Re-import to backfill:** dashboard **Import schedule** → upload workbook → choose week → **Apply**. Replace-on-import per `week_start` rebuilds aggregates and detail. Weeks imported before parser version `2` lack skipped cells, OPS detail, and raw archive.

### Backfilling historical weeks

You do **not** need to re-import each week one-by-one in the dashboard. Point the bulk script at a folder of saved Excel workbooks (e.g. your OneDrive archive). Week start is detected from sheet date headers (same as Import schedule); use `--week` only if a file is ambiguous.

**Bulk backfill won't overwrite your manual imports.** By default, any week that already has a row in `schedule_imports` is skipped — including weeks you imported and fixed one-by-one in the dashboard (unit mappings, parser tweaks, etc.). Use `--force` only when you explicitly want to replace existing data.

**Unit mappings carry forward.** When you map unknown unit codes on the Import schedule preview and click **Apply**, those mappings are saved to `unit_code_mappings` in `staffing.db`. Bulk backfill loads all saved mappings automatically, so your dashboard fixes apply to future bulk runs. You can also add mappings in `data/unit_mappings.csv` (`raw,maps_to` columns) or pass `--unit-map path/to.csv`.

**Upload retention:** dashboard uploads in `uploads/` are deleted after **7 days** (`STAFFING_UPLOAD_RETENTION_HOURS = 168`). Keep historical `.xlsx` files elsewhere and pass that path with `--dir`.

```bash
# Preview what would import (default scans uploads/ next to staffing.db)
python scripts/backfill_schedules.py --dry-run

# Fill gaps from Oct 1 onward — skips weeks you already imported
python scripts/backfill_schedules.py --dir "C:/path/to/schedule/archive" --from-date 2025-10-05

# Same as default: only import weeks missing from schedule_imports
python scripts/backfill_schedules.py --dir "C:/path/to/archive" --only-missing

# Optional extra unit mappings (merged with DB + data/unit_mappings.csv)
python scripts/backfill_schedules.py --dir "C:/path/to/archive" --unit-map data/extra_units.csv

# Re-import and REPLACE weeks that already exist (use with care)
python scripts/backfill_schedules.py --dir "C:/path/to/archive" --force
```

After a run, the script prints a summary: imported count, skipped weeks (already imported), errors, and any weeks with parser issues that need dashboard review.

### Upgrading existing weeks to person-level detail

If you already imported every week manually (CEO aggregates in `weekly_staffing`) but lack person shifts, staff roster links, manager AOC, OPS View detail, or parser v2 audit rows, use **`--upgrade-detail`** — not the default bulk skip.

```bash
# Preview which existing weeks would be re-processed
python scripts/backfill_schedules.py --dir "C:/path/to/schedule/archive" --upgrade-detail --from-date 2025-10-05 --dry-run

# Backfill detail on weeks you already have (Oct 1 onward)
python scripts/backfill_schedules.py --dir "C:/path/to/schedule/archive" --upgrade-detail --from-date 2025-10-05
```

**What gets added or refreshed:** `weekly_person_shifts`, staff roster auto-sync, `weekly_manager_shifts` (including AOC), `schedule_imports` audit, OPS View tables, parse issues, and optional raw cell archive.

**What is preserved:** manual `weekly_staffing` fields — unpartnered counts and notes, `pilot_vacancies`, `overnights_below`, `day_target`, `night_min`, and custom `notes` (anything other than the default “Imported from schedule”). CEO aggregates (filled, OT, leave, base coverage) are re-derived from the workbook.

**Workbooks required:** each week needs its `.xlsx` in `--dir` (or the stored path from a prior import if the file still exists). Dashboard `uploads/` are deleted after 7 days — keep an archive folder. Missing files report: `Week 2025-10-05: no workbook found — add file to archive folder`.

**Unit mappings:** bulk upgrade loads `unit_code_mappings` from the DB, `data/unit_mappings.csv`, and any `--unit-map` CSV, plus built-in legacy aliases (D9P/D9B/D11B → D11H). If your fixes pre-date the mappings table, re-import one week via the dashboard to seed mappings, or maintain `data/unit_mappings.csv`.

**Legacy unit codes:** older Manchester schedules used **D9P**, **D9B**, and **D11B** instead of today's **D11H**. The importer maps those to Manchester / D11H (including OT variants like D9PC). The retired **FW** unit is skipped (`skip_reason=retired_unit`) and excluded from CEO aggregates. Original cell text is kept in `raw_value`; `unit_code` is canonical for reporting.

## Django dashboard

```bash
cd bmf_staffing
python manage.py runserver
```

See **`bmf_staffing/README.md`** for features and **`bmf_staffing/bmf_staffing/settings.py`** for paths (`STAFFING_DB_PATH`, etc.).

**Manager report** (`/manager-shifts/`): line-shift counts vs the 52-shift FY minimum plus **AOC day** totals per manager. AOC cells on manager roster rows in the schedule Excel are stored on import (`event_type=aoc`) and excluded from CEO clinical aggregates. Re-import a week to backfill AOC history.

## GitHub

**Clone and run:** create a virtual environment, install **`requirements.txt`**, and run **`python -m staffing_tool init-db`** (or copy an existing `staffing.db`) before using the CLI or dashboard.

**Publish this folder as a new repository** (from the `Weekly_staffing` directory):

```bash
git init
git add .
git commit -m "Initial commit: weekly staffing tool and dashboard"
git branch -M main
git remote add origin https://github.com/cmoorem5/weekly-staffing.git
git push -u origin main
```

Use a [`.gitignore`](.gitignore) so `staffing.db`, `output/`, `uploads/`, and virtualenvs stay local (this repo already includes one).
