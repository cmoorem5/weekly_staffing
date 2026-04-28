"""
Django ORM mirrors for tables owned by SQLAlchemy in staffing.db.

Use Django Admin to edit; routing sends reads/writes to the ``staffing`` database.
"""

from django.db import models


class ManagerRosterLastName(models.Model):
    """Last names that identify manager rows (schedule cols A–B, case-insensitive)."""

    id = models.AutoField(primary_key=True)
    last_name = models.CharField(max_length=128, unique=True)

    class Meta:
        managed = False
        db_table = "manager_roster_last_name"
        verbose_name = "Manager roster (last name)"
        verbose_name_plural = "Manager roster (last names)"

    def __str__(self) -> str:
        return self.last_name
