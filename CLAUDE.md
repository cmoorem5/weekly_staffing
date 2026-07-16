# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Keep this file in sync with the code.** Before finishing any change that alters something documented here — commands, architecture, the import-pipeline gotchas, report-generator constraints, shared constants, testing conventions — update the relevant section in the same commit series. Don't touch it for changes it doesn't describe.

## What this is

Board-level staffing KPI tooling for Boston MedFlight: a standalone `staffing_tool` Python package (CLI, schedule-import parser, Excel/PDF/HTML report builders) plus a Django project (`bmf_staffing/`) with two apps — `dashboard` (the KPI/weekly-staffing workflow) and `crew_hub` (AOC Daily Report, Comm Center / duty officer scheduling, payroll, time off). Both apps sit on top of `staffing_tool`. Python 3.12+, Django 6.0.

## Commands

```bash
# Install
pip install -r requirements.txt          # runtime
pip install -r requirements-dev.txt      # + ruff for lint/format

# Lint / format (must pass before commit — CI runs these)
ruff check .
ruff format --check .          # use `ruff format .` to actually reformat

# Tests — there are TWO separate suites, run both:
python -m unittest discover -s tests -v            # staffing_tool + dashboard
python bmf_staffing/manage.py test crew_hub -v 2    # crew_hub app

# Run a single test
python -m unittest tests.test_staff_roster.RosterMergeTests.test_merge_rejects_mismatched_roles -v
python bmf_staffing/manage.py test crew_hub.tests.test_rotations -v 2

# CLI (staffing_tool) — init/migrate the DB, then subcommands like upsert-week, export-board-pack
python -m staffing_tool init-db
python -m staffing_tool --help

# Weekly / quarterly report builders (write to output/)
python scripts/build_weekly_report.py --week 2026-05-24
python scripts/build_quarterly_report.py --fy 2026 --quarter 2

# Bulk-import historical schedule workbooks (skips weeks already in schedule_imports
# unless --force; use --upgrade-detail to backfill person/roster/OPS detail on
# weeks that only have CEO aggregates)
python scripts/backfill_schedules.py --dry-run

# Run the Django dashboard
cd bmf_staffing && python manage.py runserver
```

No build step (no frontend bundler; templates are server-rendered Django/Bootstrap). The Windows deployment serves via waitress + whitenoise (`scripts/launch_crew_hub.ps1`), so `Update_Crew_Hub.bat` runs `collectstatic`; `manage.py runserver` remains the dev workflow and needs no collectstatic (`WHITENOISE_USE_FINDERS` under `DEBUG`).

## Architecture

### Two databases, one Django project

`bmf_staffing/bmf_staffing/settings.py` wires up **two** SQLite databases:

- `default` (`db.sqlite3`) — normal Django-managed tables: auth, and everything under `crew_hub` (schedules, rotations, payroll, time off).
- `staffing` (`staffing.db`, alongside this README at repo root) — the **legacy `staffing_tool` pipeline**, owned by SQLAlchemy (`staffing_tool/db.py`, `staffing_tool/models.py`). Django never migrates this file.

A handful of tables in `staffing.db` (`ManagerRosterLastName`, `StaffRosterEntry`, `TrainingCode`) have **both** a SQLAlchemy model (in `staffing_tool/models.py`, used by the parser/CLI) **and** a `managed = False` Django model mirror (in `bmf_staffing/dashboard/models.py`, used only so the dashboard's Settings pages can `.objects.using("staffing")` for simple CRUD). `dashboard/db_router.py` (`StaffingDbRouter`) is what routes those specific model names to the `staffing` alias and blocks Django `migrate` from touching it — when adding a new mirrored table, it must be added to `_STAFFING_MIRROR_MODELS` in that router or reads/writes silently go to the wrong database.

**Gotcha:** `.objects.using("staffing")` always hits the real `STAFFING_DB_PATH` from settings — it is not affected by patching a view module's `DB_PATH` constant in tests. Most `dashboard` views mix both access patterns (SQLAlchemy `session_scope(DB_PATH)` for some actions, Django ORM for others in the same view function), which is intentional but easy to trip over when testing: check which ORM a given code path actually uses before assuming a patched `DB_PATH` will be visible to it.

### staffing_tool: schedule import pipeline

The core, most subtle piece of the codebase is the RN/Medic/EMT schedule-workbook parser in `staffing_tool/schedule_import.py`, called by `apply_schedule_workbook` (`schedule_apply.py`) from the dashboard's Import Schedule view and from `scripts/backfill_schedules.py`. Rough flow per cell: `_parse_grid` walks a fixed row/column window per sheet block (RN, Medic, EMT are separate blocks with their own row ranges) → classifies each cell (staffed unit / leave code / training code / OT suffix / admin marker / manager-row / retired unit) → `aggregate_week_from_records` rolls the whole week up into `WeeklyStaffing` + `WeeklyBaseCoverage` fields → `sync_roster_from_import` / `weekly_person_shift_mappings` persist per-person detail and auto-add new staff to the roster.

Things that look like they should be simple but aren't, because real schedule workbooks are messy:

- **Never locate structural rows (OPEN/EXTRA summary rows, footers) by a hardcoded Excel row number.** Adding staff to a sheet pushes every row below it down, silently breaking a fixed row number — this actually happened (see `_find_non_person_skip_row`). Detect by the row's label text instead.
- **Skip categories vs. counted categories:** most non-staffing cells (`AOC`, `SM`, admin markers, retired units) get `skip_reason` set and `included_in_aggregates=False` — truly dropped. Training codes are the one skip category that still counts, via `WeeklyStaffing.training_shifts` (see `_append_skipped_shift`, `_person_shift_event_type`). Manager-row cells are always excluded from weekly aggregates regardless of category, tracked separately in `weekly_manager_shifts`.
- **Known-code lists are additive, not authoritative.** `SKIP_TRAINING_VALUES`/`SKIP_CELL_VALUES` are built-in defaults; Settings → Training codes (`staffing_tool/training_codes.py`, `TrainingCode` table) lets non-developers add more without a code change, merged in at parse time via `extra_training_codes`. `unit_code_mappings` (Settings → handled inline on the Import Schedule review page) does the same for unknown unit codes, merged via `resolve_unit_overrides`.
- **Roster auto-add matches on (role, last_name, first_name).** `add_roster_entries` treats a same-role/last-name row that's missing a first name as *the same person* as a later import that provides one — it fills in the blank instead of creating a duplicate, and preserves active/inactive status while doing so (filling in a name must never resurrect someone who was deliberately deactivated). Settings → Staff roster surfaces any duplicate pairs that predate this fix with a one-click merge (reassigns `WeeklyPersonShift.staff_member_id`, then deletes the extra row).
- **Manual week-edit corrections that fail validation must re-render, not redirect.** `week_edit`/`week_add` (`bmf_staffing/dashboard/views/weeks.py`) only redirect to the week list when `_save_week_and_coverage` actually persisted; a validation failure (e.g. `notes_required`, or staffing a base with no configured RW/GR total) re-renders the same form with the user's input intact instead of silently discarding it.
- **Legacy weeks store OT only in the aggregate columns.** Weeks imported/backfilled before the day/night OT split have zeros in `ot_*_day`/`ot_*_night` and their real totals in `ot_rn`/`ot_medic`/`ot_emt`/`ot_shifts`; `compute_week_metrics` falls back through that chain, and `_save_week_and_coverage` preserves the legacy totals when an edit is submitted with all day/night OT inputs at zero (see `tests/test_week_edit_legacy_ot.py`). Any new write path must keep both column sets in sync the way `schedule_apply.py` does.
- **Base coverage is capped at each base's weekly plan on both import paths.** `_cap_base_coverage_split` (`MAX_RW_UNIT_DAYS_PER_WEEK`/`MAX_GR_UNIT_DAYS_PER_WEEK`, night reduced first) applies to OPS View counts and grid-derived counts alike, so opportunistic extra vehicles (Bedford `GR2`/`NG2`) never report >100% base coverage or inflate the fixed-denominator system GR %; those unit codes are also excluded from role fill via `metrics.EXTRA_UNIT_CODES`.
- **Role fill counts seats (grid cells), not person rows.** EMT partner rows list two people in columns A–B for one grid cell, and `weekly_person_shift_mappings` writes one `WeeklyPersonShift` row per person; `metrics.compute_role_fill` dedupes worked shifts on (role, week, date, source tab, source cell) so a pair-staffed seat-day counts once against the seat-based `ROLE_CAPACITY_PER_WEEK` (EMT 49 = 7 required lines × 7 days). Rows without cell provenance count individually.

### Report generator constraints (non-negotiable)

From `docs/report-generator-spec.md`, enforced by a Cursor rule (`.cursor/rules/report-generator.mdc`) when touching the Board_Summary/Weekly_Detail Excel generator or any `report*`/`generator*` module:

- Never use cross-sheet formulas between Board_Summary and Weekly_Detail, or SUM/AVERAGE formulas on generated values.
- Never use Excel conditional formatting for status colors — compute the color in code.
- Never use the words "Green/Yellow/Red" in user-facing status cells — use "On target / Monitor / Action needed".
- Never sum raw shift counts across bases (spec §3.3).
- System GR denominator is a fixed operational cap of 28 (21 day + 7 night across NG/LG/MG), not the sum of per-base GR totals — a base with no configured total (e.g. an opportunistic/extra base) is not part of that cap.
- Unpartnered and per-role OT counts are ungraded metrics — no color, no target, no status badge.

Shared display/staffing constants live in `staffing_tool/metrics.py` — `BASE_DISPLAY_ORDER` (base order for report tables), `ROLE_CAPACITY_PER_WEEK`, `REQUIRED_DAY/NIGHT/TOTAL`, `TOTAL_PERSON_SHIFTS`, `SYSTEM_GR_MAX_SHIFTS_PER_WEEK`. Import them; don't redeclare literals in report modules (the Excel/PDF/HTML builders all already import from there). The "no Excel conditional formatting" rule applies to heat shading too — `monthly_report._heat_fill_and_font` computes gradient fills per cell in code.

### Data safety

Before any destructive write (applying a schedule import, which replaces that week's data, or deleting a week), the dashboard snapshots `staffing.db` to `archive/staffing_autobackup_<timestamp>.db` (`backup_staffing_db_before_write`, keeps the most recent `STAFFING_BACKUP_KEEP`, default 30; manual backups are never pruned).

## Testing conventions

The root `tests/` suite is plain `unittest`, not pytest, and is **not** Django's test runner — files that touch `dashboard` views manually do `sys.path.insert(...)`, set `DJANGO_SETTINGS_MODULE`, and call `django.setup()` before importing anything from `dashboard`. Copy that boilerplate from an existing test file (e.g. `tests/test_week_edit_save.py`) rather than re-deriving it. Tests that only exercise `staffing_tool` (no Django import) skip all of that.

Tests needing an isolated SQLite file use `tempfile.TemporaryDirectory()` + `staffing_tool.db.init_db(db_path)`, then `patch.object(<module>, "DB_PATH", db_path)` on every view module whose `DB_PATH` the code path under test actually reads (see the dual-ORM gotcha above — Django-ORM-only view code needs no such patch and will hit the real `staffing.db` instead).

`crew_hub` has its own Django `TestCase`-based suite under `bmf_staffing/crew_hub/tests/`, run via `manage.py test crew_hub`, separate from the root suite.
