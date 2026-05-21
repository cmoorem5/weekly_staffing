"""
Staffing data lives in ``staffing.db`` (SQLite) via SQLAlchemy in ``staffing_tool``,
not Django ORM. Schema migrations for that file are implemented in
``staffing_tool.db`` (e.g. ``migrate_unpartnered_note_columns``) and applied from
``init_db()``.

Boot path (fresh clone → ``manage.py runserver``): Django loads this app →
:meth:`DashboardConfig.ready` runs → ``init_db(STAFFING_DB_PATH)`` creates/updates
tables and columns so the first HTTP request does not depend on running the
``staffing_tool`` CLI first.

Views still call ``init_db`` via ``_ensure_db()`` for the same idempotent
upgrade path if code paths skip ``ready`` (e.g. some tests).
"""

from django.apps import AppConfig


class DashboardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "dashboard"
    verbose_name = "BMF Staffing Dashboard"

    def ready(self) -> None:
        from django.conf import settings
        from staffing_tool.db import ensure_db_ready

        db_path = getattr(settings, "STAFFING_DB_PATH", None)
        if db_path:
            ensure_db_ready(db_path)
