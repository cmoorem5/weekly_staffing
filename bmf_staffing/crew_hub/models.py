"""
Crew Hub models.

Two groups:

* Living schedules — Comm Center scheduler, duty officer rotation, and the
  vehicle status board. These persist across days and are edited any time.
* AOC Daily Report — one locked snapshot per day, seeded from the living
  schedules plus the crew shift skeleton, then edited and submitted.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from . import shifts

# ---------------------------------------------------------------------
# Living schedules
# ---------------------------------------------------------------------


class DutyOfficer(models.Model):
    """A person who can appear in the duty officer rotation."""

    name = models.CharField(max_length=128, unique=True)
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class DutyAssignment(models.Model):
    """One duty role seat on one date. MDOC may carry two rows (two names)."""

    date = models.DateField(db_index=True)
    role = models.CharField(max_length=16, choices=shifts.DUTY_ROLE_CHOICES)
    officer = models.ForeignKey(
        DutyOfficer, null=True, blank=True, on_delete=models.CASCADE
    )
    display_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Free-text name when the person is not in the roster.",
    )
    note = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["date", "role", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "role", "officer"],
                name="uniq_duty_assignment_officer",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.role}: {self.name}"

    @property
    def name(self) -> str:
        return self.display_name or (self.officer.name if self.officer else "")


class CommStaffMember(models.Model):
    """Comm Center specialist available for seat assignments."""

    name = models.CharField(max_length=128, unique=True)
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["name"]
        verbose_name = "Comm Center staff member"

    def __str__(self) -> str:
        return self.name


class CommShiftAssignment(models.Model):
    """One Comm Center seat on one date."""

    date = models.DateField(db_index=True)
    seat = models.CharField(max_length=8, choices=shifts.COMM_SEAT_CHOICES)
    member = models.ForeignKey(
        CommStaffMember, null=True, blank=True, on_delete=models.CASCADE
    )
    display_name = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Free-text name when the person is not in the roster.",
    )
    note = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["date", "seat"]
        constraints = [
            models.UniqueConstraint(fields=["date", "seat"], name="uniq_comm_seat_day"),
        ]

    def __str__(self) -> str:
        return f"{self.date} {self.seat}: {self.name}"

    @property
    def name(self) -> str:
        return self.display_name or (self.member.name if self.member else "")


class Vehicle(models.Model):
    """Fleet vehicle with its live status (carries forward day to day)."""

    identifier = models.CharField(max_length=16, unique=True)
    category = models.CharField(
        max_length=2, choices=shifts.VEHICLE_CATEGORY_CHOICES
    )
    display_order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    current_status = models.CharField(max_length=128, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "display_order", "identifier"]

    def __str__(self) -> str:
        return self.identifier

    @classmethod
    def ensure_fleet(cls) -> None:
        """Create any missing fleet vehicles from the constants module."""
        existing = set(cls.objects.values_list("identifier", flat=True))
        to_create = [
            cls(identifier=identifier, category=category, display_order=order)
            for order, (identifier, category) in enumerate(shifts.FLEET)
            if identifier not in existing
        ]
        if to_create:
            cls.objects.bulk_create(to_create)


class VehicleStatusLog(models.Model):
    """History of vehicle status changes made on the board."""

    vehicle = models.ForeignKey(
        Vehicle, on_delete=models.CASCADE, related_name="status_logs"
    )
    status = models.CharField(max_length=128, blank=True, default="")
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]

    def __str__(self) -> str:
        return f"{self.vehicle.identifier}: {self.status} @ {self.changed_at:%Y-%m-%d %H:%M}"


# ---------------------------------------------------------------------
# AOC Daily Report
# ---------------------------------------------------------------------


class DailyReport(models.Model):
    STATUS_DRAFT = "draft"
    STATUS_SUBMITTED = "submitted"
    STATUS_CHOICES = [(STATUS_DRAFT, "Draft"), (STATUS_SUBMITTED, "Submitted")]

    report_date = models.DateField(unique=True)
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_DRAFT
    )
    weather = models.CharField(max_length=256, blank=True, default="")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-report_date"]
        permissions = [
            ("reopen_report", "Can reopen a submitted AOC daily report"),
        ]

    def __str__(self) -> str:
        return f"AOC Daily Report {self.report_date} ({self.status})"

    @property
    def is_submitted(self) -> bool:
        return self.status == self.STATUS_SUBMITTED


class DutyRosterEntry(models.Model):
    """Snapshot of one duty officer seat for the report day."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="duty_entries"
    )
    role = models.CharField(max_length=16, choices=shifts.DUTY_ROLE_CHOICES)
    name = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "role"], name="uniq_duty_entry_per_report"
            ),
        ]
        verbose_name_plural = "Duty roster entries"

    def __str__(self) -> str:
        return f"{self.role}: {self.name}"


class CrewEntry(models.Model):
    """One position on one shift for the report day."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="crew_entries"
    )
    base = models.CharField(max_length=4, choices=shifts.BASE_CHOICES)
    shift_code = models.CharField(max_length=8, choices=shifts.SHIFT_CHOICES)
    position = models.CharField(max_length=8, choices=shifts.POSITION_CHOICES)
    name = models.CharField(max_length=128, blank=True, default="")
    ref_flag = models.BooleanField(
        default=False, help_text="Marks a confirmed open position (renders OPEN)."
    )

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "Crew entries"

    def __str__(self) -> str:
        return f"{self.base} {self.shift_code} {self.position}: {self.name}"

    def clean(self) -> None:
        if not shifts.is_valid_crew_combo(self.base, self.shift_code, self.position):
            raise ValidationError(
                f"Invalid base/shift/position combination: "
                f"{self.base}/{self.shift_code}/{self.position}"
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class ExtraEntry(models.Model):
    """Floating ride-alongs, orientees, and Plymouth Ground bonus crew."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="extra_entries"
    )
    base = models.CharField(
        max_length=4, choices=shifts.BASE_CHOICES, blank=True, default=""
    )
    shift_code = models.CharField(
        max_length=8, choices=shifts.SHIFT_CHOICES, blank=True, default=""
    )
    role = models.CharField(
        max_length=8, choices=shifts.POSITION_CHOICES, blank=True, default=""
    )
    name = models.CharField(max_length=128, blank=True, default="")
    note = models.CharField(max_length=256, blank=True, default="")

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "Extra entries"

    def __str__(self) -> str:
        return f"{self.name} ({self.note or self.role or 'extra'})"


class CommCenterEntry(models.Model):
    """Snapshot of one Comm Center seat for the report day."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="comm_entries"
    )
    seat = models.CharField(max_length=8, choices=shifts.COMM_SEAT_CHOICES)
    name = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "seat"], name="uniq_comm_entry_per_report"
            ),
        ]
        verbose_name_plural = "Comm Center entries"

    def __str__(self) -> str:
        return f"{self.seat}: {self.name}"


class SickLateEntry(models.Model):
    TYPE_SICK = "sick_call"
    TYPE_LATE = "late_arrival"
    TYPE_CHOICES = [(TYPE_SICK, "Sick call"), (TYPE_LATE, "Late arrival")]

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="sick_late_entries"
    )
    entry_type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    text = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "Sick / late entries"

    def __str__(self) -> str:
        return f"{self.get_entry_type_display()}: {self.text[:40]}"


class VehicleStatusEntry(models.Model):
    """Snapshot of one vehicle's status for the report day."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="vehicle_entries"
    )
    vehicle_id = models.CharField(max_length=16)
    category = models.CharField(
        max_length=2, choices=shifts.VEHICLE_CATEGORY_CHOICES
    )
    status = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "vehicle_id"], name="uniq_vehicle_entry_per_report"
            ),
        ]
        verbose_name_plural = "Vehicle status entries"

    def __str__(self) -> str:
        return f"{self.vehicle_id}: {self.status}"


class TransportSummary(models.Model):
    """Daily transport counts filled by the AOC (pending + complex calls)."""

    report = models.OneToOneField(
        DailyReport, on_delete=models.CASCADE, related_name="transport_summary"
    )
    pending_count = models.PositiveIntegerField(default=0)
    complex_calls = models.TextField(
        blank=True, default="", verbose_name="Complex / complicated logistical calls"
    )

    class Meta:
        verbose_name_plural = "Transport summaries"

    def __str__(self) -> str:
        return f"Transports for {self.report.report_date}"

    @property
    def completed_total(self) -> int:
        return sum(
            row.gcct + row.rw for row in self.report.transport_base_counts.all()
        )


class TransportBaseCount(models.Model):
    """Completed transports by base, split GCCT / RW."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="transport_base_counts"
    )
    base = models.CharField(max_length=4, choices=shifts.BASE_CHOICES)
    gcct = models.PositiveIntegerField(default=0)
    rw = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "base"], name="uniq_transport_base_per_report"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.base}: GCCT {self.gcct} / RW {self.rw}"

    @property
    def total(self) -> int:
        return self.gcct + self.rw


class PendingTransport(models.Model):
    """One pending transport row (call type / asset / status / location)."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="pending_transports"
    )
    order = models.PositiveSmallIntegerField(default=0)
    call_type = models.CharField(max_length=64, blank=True, default="")
    asset = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=64, blank=True, default="")
    location = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        ordering = ["order", "id"]

    def __str__(self) -> str:
        return f"Pending #{self.order}: {self.call_type} {self.asset}"


class MissCategoryCount(models.Model):
    """System-miss count for one (editable) category label."""

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="miss_counts"
    )
    order = models.PositiveSmallIntegerField(default=0)
    label = models.CharField(max_length=64, blank=True, default="")
    count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name_plural = "Miss category counts"

    def __str__(self) -> str:
        return f"{self.label}: {self.count}"


class ReportAuditLog(models.Model):
    """Submit / reopen / email events for a report."""

    ACTION_SUBMITTED = "submitted"
    ACTION_REOPENED = "reopened"
    ACTION_EMAIL_SENT = "email_sent"
    ACTION_EMAIL_FAILED = "email_failed"
    ACTION_CHOICES = [
        (ACTION_SUBMITTED, "Submitted"),
        (ACTION_REOPENED, "Reopened"),
        (ACTION_EMAIL_SENT, "Email sent"),
        (ACTION_EMAIL_FAILED, "Email failed"),
    ]

    report = models.ForeignKey(
        DailyReport, on_delete=models.CASCADE, related_name="audit_logs"
    )
    action = models.CharField(max_length=16, choices=ACTION_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    detail = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"{self.report.report_date} {self.action} @ {self.timestamp:%Y-%m-%d %H:%M}"
