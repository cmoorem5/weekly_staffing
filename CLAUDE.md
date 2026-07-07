# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Boston MedFlight (BMF) staffing tooling, two halves sharing one repo:

1. **`staffing_tool/`** — plain-Python package (SQLAlchemy + openpyxl + reportlab): DB models, KPI metrics, schedule Excel import, Excel/PDF report builders, CLI (`python -m staffing_tool`). Owns **`staffing.db`** (SQLite, repo root, gitignored).
2. **`bmf_staffing/`** — Django 6 project with two apps:
   - **`dashboard`** — web UI over the same `staffing.db` via the `staffing_tool` package (weeks, base totals, schedule import, KPI reports, DB backups).
   - **`crew_hub`** — self-contained multi-user scheduling app (comm center / duty officer calendars, rotations, vehicle board, AOC Daily Report email, payroll CSVs, time off, notifications). Uses Django ORM/auth on its own database.

Python 3.12+ required (Django 6.0).

## Commands

All commands run from the repo root unless noted.

```bash
pip install -r requirements-dev.txt      # runtime deps + ruff (pinned)

# Lint / format (CI enforces both)
ruff check .
ruff format --check .                    # drop --check to apply

# Core test suite (unittest, not pytest)
python -m unittest discover -s tests -v
python -m unittest tests.test_metrics -v                     # one module
python -m unittest tests.test_metrics.ComputeWeekMetricsTests.test_staffing_rate_and_ot_dependency

# Crew Hub tests (Django test runner — crew_hub tests live in the app, not tests/)
python bmf_staffing/manage.py test crew_hub -v 2
python bmf_staffing/manage.py test crew_hub.tests.test_rotations

# Run the dashboard
cd bmf_staffing && python manage.py runserver     # http://127.0.0.1:8000/, Crew Hub at /hub/

# CLI + database
python -m staffing_tool init-db          # create/migrate staffing.db
python -m staffing_tool --help

# Standalone reports (run scripts/setup_report_fonts.py once first)
python scripts/build_weekly_report.py [--week 2026-05-24]
python scripts/build_quarterly_report.py [--fy 2026 --quarter 2]
python scripts/backfill_schedules.py --dir <archive> [--dry-run|--force|--upgrade-detail]

# Crew Hub demo data (fake names; never touches submitted reports)
python bmf_staffing/manage.py seed_aoc_demo --date 2026-07-02
```

CI (`.github/workflows/ci.yml`) = ruff check + ruff format --check + both test suites on Python 3.12.

The root-level `.bat` files and `Update_Crew_Hub.bat` are Windows end-user launchers (the tool runs on managers' laptops); keep them working when changing setup/run steps.

## Architecture

### Two databases, two ORMs — never mix them

- **`staffing.db`** (repo root) is owned by **SQLAlchemy** (`staffing_tool/models.py`, `staffing_tool/db.py`). Schema changes go through `staffing_tool.db` migration helpers (`migrate_*` functions called from `init-db`), **not** Django migrations.
- **`bmf_staffing/db.sqlite3`** is the Django default DB: auth, sessions, and everything in `crew_hub` (optionally PostgreSQL via `DJANGO_DB_ENGINE=postgresql`; the legacy `staffing` alias stays SQLite either way).
- Django also registers `staffing.db` as the `"staffing"` database alias, but only for two **mirror models** (`ManagerRosterLastName`, `StaffRosterEntry`) exposed in Django admin. `dashboard/db_router.py` routes them and blocks Django from ever migrating `staffing.db`. Add to `_STAFFING_MIRROR_MODELS` if you mirror another table.
- Dashboard views do real work by importing `staffing_tool` directly and opening SQLAlchemy sessions (`session_scope` from `dashboard/views/helpers.py` patterns) — not through Django models.

### staffing_tool data flow

Schedule Excel import is the heart: `schedule_import.py` (parse) → `schedule_apply.py` / `schedule_persistence.py` (replace-on-import per `week_start`: CEO aggregate tables + person-level detail + `schedule_imports` audit row with SHA-256 and `parser_version`). `metrics.py` computes KPI rates, `rag.py` grades them against `kpi_thresholds`, and `report.py` / `weekly_pdf_report.py` / `quarterly_pdf_report.py` (styled by `report_style.py`) render outputs. Query helpers for detail tables live in `schedule_data.py`. Canonical base list is `DEFAULT_BASES` in `db.py` — dashboard and importer both derive from it.

Before any destructive write (import apply, week delete, restore) the code snapshots `staffing.db` to `archive/` via `db_backup.py`; keep that behavior when adding destructive operations.

### Crew Hub

Views are split by feature under `crew_hub/views/`; business logic lives in modules next to models (`shifts.py`, `services.py`, `payroll.py`, `notify.py`, `emailer.py`). Permissions: viewing needs login only; editing needs `crew_hub.manage_schedules`; unlocking a submitted AOC report needs `crew_hub.reopen_report` (a "Crew Hub Managers" group with both is created by migration 0005). Rotation auto-fill must never overwrite manually assigned days, and approved time off never auto-deletes scheduled shifts — conflicts are flagged instead. Email defaults to the console backend; config comes from a repo-root `.env` (see `.env.example`). Azure AD SSO is deliberately deferred — `TODO` stubs in `settings.py` mark the swap points.

### Tests

Root `tests/` use stdlib `unittest`; files insert the repo root (and `bmf_staffing/`) onto `sys.path` and, for view smoke tests, call `django.setup()` themselves — follow the existing header pattern in e.g. `tests/test_views_smoke.py`. Tests create temp databases; nothing touches the real `staffing.db`. Ruff per-file ignores already allow the `E402` this causes in `scripts/` and `tests/`.

## Report generator rules (non-negotiable)

From `docs/report-generator-spec.md` (§8 "What NOT to do") and `.cursor/rules/report-generator.mdc` — applies to the Excel/PDF weekly & quarterly report builders:

- Never use cross-sheet formulas between Board_Summary and Weekly_Detail, or SUM/AVERAGE formulas on generated values — the generator writes literal values.
- Never use Excel conditional formatting for status colors; compute colors in code.
- User-facing status text is "On target / Monitor / Action needed" — never "Green/Yellow/Red".
- Never sum raw shift counts across bases (§3.3).
- System GR denominator is **28, not 35** (§1.3) — it's an operational budget cap, not configured-cells × 7.
- Unpartnered and per-role OT counts are ungraded metrics: no color, no target, no status badge (§1.5).
- KPI thresholds in the generator must stay in sync with the dashboard's RAG logic and the `kpi_thresholds` table.

Visual styling for reports follows `docs/BMF_Visual_Style_Spec.md` (Barlow / IBM Plex Mono fonts in `fonts/`, loaded by `report_style.py`).
