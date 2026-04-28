"""Django Admin registrations."""

from django.contrib import admin

from .models import ManagerRosterLastName

# Add a small "Admin tools" section to the admin index page.
admin.site.index_template = "dashboard/admin_index.html"


@admin.register(ManagerRosterLastName)
class ManagerRosterLastNameAdmin(admin.ModelAdmin):
    list_display = ("last_name",)
    search_fields = ("last_name",)
    ordering = ("last_name",)
