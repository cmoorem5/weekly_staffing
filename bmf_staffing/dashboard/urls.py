"""Dashboard URL routes.

Static paths like week/add/ must be registered before week/<str:week_start>/
so that 'add' is not captured as a week_start slug.
"""

from django.urls import path
from django.views.generic import RedirectView

from .views import (
    backup_db,
    base_totals,
    coverage_heatmap,
    database_backups,
    export_excel,
    home,
    import_review,
    import_schedule,
    kpi_thresholds_settings,
    manager_roster_settings,
    manager_shifts,
    manager_shifts_export_csv,
    manager_shifts_export_xlsx,
    monthly_report,
    person_ops_export_csv,
    person_ops_report,
    quarterly_staffing_report,
    reports_index,
    restore_db,
    settings_index,
    staff_roster_settings,
    staffing_dashboard,
    staffing_dashboard_export_csv,
    staffing_dashboard_export_xlsx,
    week_add,
    week_delete,
    week_edit,
    week_list,
    weekly_report_download_html,
    weekly_report_download_pdf,
    weekly_staffing_report,
)

urlpatterns = [
    # Schedule-first: the app lands on today's schedule board (Crew Hub).
    path(
        "",
        RedirectView.as_view(pattern_name="crew_hub:hub_home"),
        name="root",
    ),
    # KPI overview (the former home page) stays available in the sidebar.
    path("overview/", home, name="home"),
    path("reports/", reports_index, name="reports_index"),
    path("reports/coverage-heatmap/", coverage_heatmap, name="coverage_heatmap"),
    path("settings/", settings_index, name="settings_index"),
    path(
        "settings/manager-roster/",
        manager_roster_settings,
        name="manager_roster_settings",
    ),
    path("settings/staff-roster/", staff_roster_settings, name="staff_roster_settings"),
    path(
        "settings/kpi-thresholds/",
        kpi_thresholds_settings,
        name="kpi_thresholds_settings",
    ),
    path("admin-tools/backup-db/", backup_db, name="backup_db"),
    path("admin-tools/restore-db/", restore_db, name="restore_db"),
    path("settings/backups/", database_backups, name="database_backups"),
    path("staffing-dashboard/", staffing_dashboard, name="staffing_dashboard"),
    path(
        "staffing-dashboard/export.csv",
        staffing_dashboard_export_csv,
        name="staffing_dashboard_export_csv",
    ),
    path(
        "staffing-dashboard/export.xlsx",
        staffing_dashboard_export_xlsx,
        name="staffing_dashboard_export_xlsx",
    ),
    path("base-totals/", base_totals, name="base_totals"),
    path("weeks/", week_list, name="week_list"),
    path("import-schedule/", import_schedule, name="import_schedule"),
    path("import-review/", import_review, name="import_review"),
    path("week/add/", week_add, name="week_add"),
    path(
        "week/<str:week_start>/",
        week_edit,
        name="week_edit",
    ),
    path(
        "week/<str:week_start>/delete/",
        week_delete,
        name="week_delete",
    ),
    path(
        "export/<str:week_start>/",
        export_excel,
        name="export_excel",
    ),
    path("report/monthly/", monthly_report, name="monthly_report"),
    path("report/weekly/", weekly_staffing_report, name="weekly_staffing_report"),
    path(
        "report/weekly/<str:week_start>/pdf/",
        weekly_report_download_pdf,
        name="weekly_report_download_pdf",
    ),
    path(
        "report/weekly/<str:week_start>/html/",
        weekly_report_download_html,
        name="weekly_report_download_html",
    ),
    path(
        "report/quarterly/", quarterly_staffing_report, name="quarterly_staffing_report"
    ),
    path("manager-shifts/", manager_shifts, name="manager_shifts"),
    path(
        "manager-shifts/export.csv",
        manager_shifts_export_csv,
        name="manager_shifts_export_csv",
    ),
    path(
        "manager-shifts/export.xlsx",
        manager_shifts_export_xlsx,
        name="manager_shifts_export_xlsx",
    ),
    path("ops/person/", person_ops_report, name="person_ops_report"),
    path(
        "ops/person/export.csv",
        person_ops_export_csv,
        name="person_ops_export_csv",
    ),
]
