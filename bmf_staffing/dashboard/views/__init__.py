"""Dashboard views package (split from monolithic views.py)."""

from .admin_tools import backup_db, restore_db
from .home import home
from .import_schedule import import_schedule
from .manager_shifts import manager_shifts
from .monthly_report import monthly_report
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
    "manager_shifts",
    "monthly_report",
    "restore_db",
    "staffing_dashboard",
    "staffing_dashboard_export_csv",
    "staffing_dashboard_export_xlsx",
    "week_add",
    "week_delete",
    "week_edit",
    "week_list",
]
