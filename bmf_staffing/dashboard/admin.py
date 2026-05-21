"""Django Admin registrations."""

from django.contrib import admin

# Manager roster is edited in-app: Settings → Manager roster.
# Django admin index still links to Settings hub and backup tools.

admin.site.index_template = "dashboard/admin_index.html"
