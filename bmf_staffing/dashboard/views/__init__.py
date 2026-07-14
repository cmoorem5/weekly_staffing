"""Dashboard views package (split from monolithic views.py)."""

from .admin_tools import backup_db, database_backups, restore_db
from .coverage_heatmap import coverage_heatmap
from .home import home
from .import_review import import_review
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
    training_codes_settings,
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
    "database_backups",
    "base_totals",
    "coverage_heatmap",
    "export_excel",
    "home",
    "import_review",
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
    "training_codes_settings",
    "staffing_dashboard",
    "staffing_dashboard_export_csv",
    "staffing_dashboard_export_xlsx",
    "week_add",
    "week_delete",
    "week_edit",
    "week_list",
]
