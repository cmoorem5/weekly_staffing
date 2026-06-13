"""Dashboard views package (split from monolithic views.py)."""

from .admin_tools import backup_db, restore_db
from .home import home
from .import_schedule import import_schedule
from .manager_shifts import (
    manager_shifts,
    manager_shifts_export_csv,
    manager_shifts_export_xlsx,
)
from .monthly_report import monthly_report
from .person_ops import person_ops_export_csv, person_ops_report
from .quarterly_staffing_report import quarterly_staffing_report
from .reports import reports_index
from .settings_views import (
    kpi_thresholds_settings,
    manager_roster_settings,
    settings_index,
    staff_roster_settings,
)
from .staffing_dashboard import (
    staffing_dashboard,
    staffing_dashboard_export_csv,
    staffing_dashboard_export_xlsx,
)
from .weekly_staffing_report import (
    weekly_report_download_html,
    weekly_report_download_pdf,
    weekly_staffing_report,
)
from .weeks import (
    base_totals,
    export_excel,
    week_add,
    week_delete,
    week_edit,
    week_list,
)

__all__ = [
    "backup_db",
    "base_totals",
    "export_excel",
    "home",
    "import_schedule",
    "kpi_thresholds_settings",
    "manager_roster_settings",
    "staff_roster_settings",
    "manager_shifts",
    "manager_shifts_export_csv",
    "manager_shifts_export_xlsx",
    "monthly_report",
    "person_ops_export_csv",
    "person_ops_report",
    "quarterly_staffing_report",
    "reports_index",
    "weekly_report_download_html",
    "weekly_report_download_pdf",
    "weekly_staffing_report",
    "restore_db",
    "settings_index",
    "staffing_dashboard",
    "staffing_dashboard_export_csv",
    "staffing_dashboard_export_xlsx",
    "week_add",
    "week_delete",
    "week_edit",
    "week_list",
]
