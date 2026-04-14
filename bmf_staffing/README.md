# BMF Staffing – Django dashboard

Web UI for the Boston MedFlight board staffing KPI workflow. Uses the same `staffing.db` SQLite database and **`staffing_tool`** package as the command-line tool in the parent folder.

## Run locally

1. From the **`Weekly_staffing`** folder (repository root), install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Start the server:

   - Double-click **`Run_Staffing_Django.bat`** (in `Weekly_staffing`), or  
   - In a terminal:

     ```bash
     cd bmf_staffing
     python manage.py runserver
     ```

3. Open **http://127.0.0.1:8000/**

## What you can do

- **Home** – Recent weeks, KPI overview, links to other pages.
- **Base totals** – Set RW/GR unit-days per week per base (Bedford, Lawrence, Mansfield, Manchester, Plymouth).
- **Weeks** – List weeks with staffing rate, OT %, leave %, overnights below, RAG. Open a week to edit, export Excel, or open the position grid.
- **Import schedule** – Upload the schedule workbook; preview parsed shifts, then apply to a week.
- **Add week** – Create a week (Sunday `YYYY-MM-DD`) and enter coverage manually.
- **Monthly report** – Pick a date range and download the aggregated Excel workbook.
- **Export Excel** (from a week) – Downloads the board pack for that week (`staffing_tool.report`).

Data lives in **`staffing.db`** in the parent **`Weekly_staffing`** folder. Exports go to the **`output`** folder there (created as needed).

## Staffing by position (CEO grid)

For each week, the **Position grid** has one row per vehicle slot (e.g. BR-D, BG-N), columns RN / Medic / Pilot / EMT. Enter **1** when filled, or a reason (**sick**, **LOA**, **AT**, **LT**, **vacant**, etc.). The Excel export includes a **Staffing_By_Position** sheet. From **Weeks**, use **Position grid** for that week.

If the database predates this feature, from **`Weekly_staffing`** run:

```bash
python -m staffing_tool init-db
```

(or `migrate` if tables already exist) so vehicle slots and related tables are present.
