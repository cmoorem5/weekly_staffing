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


class TrainingCode(models.Model):
    """Admin-added training/education codes (Settings > Training codes)."""

    id = models.AutoField(primary_key=True)
    code = models.CharField(max_length=64, unique=True)
    created_at = models.CharField(max_length=32, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "training_code"
        verbose_name = "Training code"
        verbose_name_plural = "Training codes"

    def __str__(self) -> str:
        return self.code


class StaffRosterEntry(models.Model):
    """RN / Medic / EMT roster for schedule import and staff ops reports."""

    id = models.AutoField(primary_key=True)
    last_name = models.CharField(max_length=128)
    first_name = models.CharField(max_length=128, blank=True, default="")
    role = models.CharField(max_length=16)
    active = models.BooleanField(default=True)
    created_at = models.CharField(max_length=32, null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        managed = False
        db_table = "staff_roster_entry"
        verbose_name = "Staff roster entry"
        verbose_name_plural = "Staff roster entries"
        unique_together = (("role", "last_name", "first_name"),)

    def __str__(self) -> str:
        first = (self.first_name or "").strip()
        if first:
            return f"{self.last_name}, {first} ({self.role})"
        return f"{self.last_name} ({self.role})"
