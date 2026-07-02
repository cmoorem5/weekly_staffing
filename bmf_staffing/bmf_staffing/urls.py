from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # Login/logout for Crew Hub views.
    # TODO: Replace with Azure AD SSO (MSAL) once IT governance approves.
    path("accounts/", include("django.contrib.auth.urls")),
    path("hub/", include("crew_hub.urls")),
    path("", include("dashboard.urls")),
]
