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
from .reports import reports_index
from .settings_views import (
    kpi_thresholds_settings,
    manager_roster_settings,
    settings_index,
)
from .staffing_dashboard import (
    staffing_dashboard,
    staffing_dashboard_export_csv,
    staffing_dashboard_export_xlsx,
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
    "manager_shifts",
    "manager_shifts_export_csv",
    "manager_shifts_export_xlsx",
    "monthly_report",
    "reports_index",
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
