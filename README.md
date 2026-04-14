# Weekly staffing (BMF)

Board-level staffing KPI tooling: a **`staffing_tool`** Python package (CLI + Excel reports) and an optional **Django dashboard** under **`bmf_staffing/`**. Data is stored in a local SQLite file **`staffing.db`** next to this README.

**Python:** 3.11+

## Repository layout

| Path | Purpose |
|------|--------|
| `staffing_tool/` | Core library: DB models, metrics, weekly/monthly Excel export, schedule import parsing, CLI (`python -m staffing_tool`) |
| `bmf_staffing/` | Django project: web UI for weeks, base totals, schedule import, monthly report |
| `requirements.txt` | Runtime dependencies (SQLAlchemy, openpyxl, Pillow, Django) |
| `requirements-dev.txt` | Adds Ruff for lint/format |
| `output/` | Generated Excel exports (gitignored) |
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

## Command line

Initialize or migrate the database, then use subcommands such as `upsert-week`, `export-board-pack`:

```bash
python -m staffing_tool init-db
python -m staffing_tool --help
```

## Django dashboard

```bash
cd bmf_staffing
python manage.py runserver
```

See **`bmf_staffing/README.md`** for features and **`bmf_staffing/bmf_staffing/settings.py`** for paths (`STAFFING_DB_PATH`, etc.).

## GitHub

**Clone and run:** create a virtual environment, install **`requirements.txt`**, and run **`python -m staffing_tool init-db`** (or copy an existing `staffing.db`) before using the CLI or dashboard.

**Publish this folder as a new repository** (from the `Weekly_staffing` directory):

```bash
git init
git add .
git commit -m "Initial commit: weekly staffing tool and dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

Use a [`.gitignore`](.gitignore) so `staffing.db`, `output/`, `uploads/`, and virtualenvs stay local (this repo already includes one).
