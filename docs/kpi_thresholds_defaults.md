# KPI thresholds — default seed (`kpi_thresholds`)

Values below match `DEFAULT_THRESHOLDS` in `staffing_tool/db.py` (inserted by `seed_kpi_thresholds` only when a metric name is **missing** from the table). Your live `staffing.db` may differ if rows were edited by hand.

| metric_name | green_min | green_max | yellow_min | yellow_max | red_min | red_max | higher_is_better |
|-------------|-----------|-----------|------------|------------|---------|---------|------------------|
| Staffing Rate | 0.95 | 1.0 | 0.90 | 0.949 | 0.0 | 0.90 | 1 |
| OT Dependency | 0.0 | 0.08 | 0.081 | 0.12 | 0.12 | 1.0 | 0 |
| Shift Exception % | 0.0 | 0.25 | 0.251 | 0.32 | 0.32 | 1.0 | 0 |
| Overnights Below Coverage | 0.0 | 1.0 | 2.0 | 3.0 | 4.0 | 999.0 | 0 |
| System RW Coverage % | 0.95 | 1.0 | 0.90 | 0.949 | 0.0 | 0.90 | 1 |
| System GR Coverage % | 0.92 | 1.0 | 0.85 | 0.919 | 0.0 | 0.849 | 1 |
| Pilot Vacancies | 0.0 | 0.0 | 1.0 | 5.0 | 6.0 | 999.0 | 0 |

**Live DB dump** (run from project root with SQLite):

```bash
python -c "import sqlite3; c=sqlite3.connect('staffing.db'); print(c.execute('SELECT metric_name, green_min, green_max, yellow_min, yellow_max, red_min, red_max, higher_is_better FROM kpi_thresholds ORDER BY metric_name').fetchall())"
```

Compare against `docs/report-generator-spec.md` §1.4 before changing seed or migrations.
