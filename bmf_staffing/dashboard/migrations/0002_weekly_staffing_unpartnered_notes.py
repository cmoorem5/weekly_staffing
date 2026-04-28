"""
Weekly staffing lives in SQLite (``staffing.db``) via SQLAlchemy, not Django ORM.

DDL for ``weekly_staffing`` is owned solely by ``staffing_tool.db`` (e.g.
``migrate_unpartnered_note_columns``), invoked from ``init_db()``. On Django
boot, ``dashboard.apps.DashboardConfig.ready()`` calls ``init_db(STAFFING_DB_PATH)``,
so ``manage.py runserver`` applies those changes without running the CLI.

This empty migration only advances Django's migration graph; it does not alter
``staffing.db``.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_manager_roster_model"),
    ]

    operations = []
