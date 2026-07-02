from django.contrib import admin

from .models import (
    CommCenterEntry,
    CommRotation,
    CommShiftAssignment,
    CommStaffMember,
    CrewEntry,
    DailyReport,
    DutyAssignment,
    DutyOfficer,
    DutyRosterEntry,
    ExtraEntry,
    MissCategoryCount,
    PendingTransport,
    ReportAuditLog,
    SickLateEntry,
    TransportBaseCount,
    TransportSummary,
    Vehicle,
    VehicleStatusEntry,
    VehicleStatusLog,
)


@admin.register(DutyOfficer)
class DutyOfficerAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "notes")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(DutyAssignment)
class DutyAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "role", "name", "note")
    list_filter = ("role",)
    date_hierarchy = "date"


@admin.register(CommStaffMember)
class CommStaffMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "notes")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(CommShiftAssignment)
class CommShiftAssignmentAdmin(admin.ModelAdmin):
    list_display = ("date", "seat", "name", "work_type", "note")
    list_filter = ("seat", "work_type")
    date_hierarchy = "date"


@admin.register(CommRotation)
class CommRotationAdmin(admin.ModelAdmin):
    list_display = (
        "member",
        "seat",
        "pattern_label",
        "anchor_date",
        "end_date",
        "active",
    )
    list_filter = ("seat", "pattern_type", "active")


class VehicleStatusLogInline(admin.TabularInline):
    model = VehicleStatusLog
    extra = 0
    readonly_fields = ("status", "changed_by", "changed_at")
    can_delete = False


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("identifier", "category", "current_status", "active", "updated_at")
    list_filter = ("category", "active")
    inlines = [VehicleStatusLogInline]


class DutyRosterEntryInline(admin.TabularInline):
    model = DutyRosterEntry
    extra = 0


class CrewEntryInline(admin.TabularInline):
    model = CrewEntry
    extra = 0


class ExtraEntryInline(admin.TabularInline):
    model = ExtraEntry
    extra = 0


class CommCenterEntryInline(admin.TabularInline):
    model = CommCenterEntry
    extra = 0


class SickLateEntryInline(admin.TabularInline):
    model = SickLateEntry
    extra = 0


class VehicleStatusEntryInline(admin.TabularInline):
    model = VehicleStatusEntry
    extra = 0


class TransportBaseCountInline(admin.TabularInline):
    model = TransportBaseCount
    extra = 0


class PendingTransportInline(admin.TabularInline):
    model = PendingTransport
    extra = 0


class MissCategoryCountInline(admin.TabularInline):
    model = MissCategoryCount
    extra = 0


@admin.register(DailyReport)
class DailyReportAdmin(admin.ModelAdmin):
    list_display = ("report_date", "status", "submitted_by", "submitted_at")
    list_filter = ("status",)
    date_hierarchy = "report_date"
    inlines = [
        DutyRosterEntryInline,
        CrewEntryInline,
        ExtraEntryInline,
        CommCenterEntryInline,
        SickLateEntryInline,
        VehicleStatusEntryInline,
        TransportBaseCountInline,
        PendingTransportInline,
        MissCategoryCountInline,
    ]


@admin.register(TransportSummary)
class TransportSummaryAdmin(admin.ModelAdmin):
    list_display = ("report", "pending_count")


@admin.register(ReportAuditLog)
class ReportAuditLogAdmin(admin.ModelAdmin):
    list_display = ("report", "action", "actor", "timestamp")
    list_filter = ("action",)
    readonly_fields = ("report", "action", "actor", "timestamp", "detail")
