"""Dashboard URL routes.

Static paths like week/add/ must be registered before week/<str:week_start>/
so that 'add' is not captured as a week_start slug.
"""

from django.urls import path

from .views import (
    backup_db,
    base_totals,
    export_excel,
    home,
    import_schedule,
    manager_shifts,
    monthly_report,
    restore_db,
    staffing_dashboard,
    staffing_dashboard_export_csv,
    staffing_dashboard_export_xlsx,
    week_add,
    week_delete,
    week_edit,
    week_list,
)

urlpatterns = [
    path("", home, name="home"),
    path("admin-tools/backup-db/", backup_db, name="backup_db"),
    path("admin-tools/restore-db/", restore_db, name="restore_db"),
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
    path("manager-shifts/", manager_shifts, name="manager_shifts"),
]
