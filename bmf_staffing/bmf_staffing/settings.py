"""
Django settings for BMF Staffing Dashboard.

Runs locally; uses existing staffing.db next to the Weekly_staffing folder.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Parent of bmf_staffing/ = Weekly_staffing (staffing_tool + staffing.db)
WEEKLY_STAFFING_ROOT = BASE_DIR.parent
STAFFING_DB_PATH = os.path.join(WEEKLY_STAFFING_ROOT, "staffing.db")

# Set DJANGO_SECRET_KEY in production; never commit a real secret.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "dev-key-change-in-production",
)
# Set DJANGO_DEBUG=0 or false in production.
DEBUG = os.environ.get("DJANGO_DEBUG", "true").lower() in (
    "1",
    "true",
    "yes",
)

ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "dashboard.apps.DashboardConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "bmf_staffing.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "dashboard.context_processors.staffing_ops_banner",
            ],
        },
    },
]
WSGI_APPLICATION = "bmf_staffing.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    },
    # Same file SQLAlchemy uses; only for Admin on ``ManagerRosterLastName``.
    "staffing": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": STAFFING_DB_PATH,
    },
}

DATABASE_ROUTERS = ["dashboard.db_router.StaffingDbRouter"]

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Staffing tool: Excel output (paths under Weekly_staffing/)
STAFFING_OUTPUT_DIR = os.path.join(WEEKLY_STAFFING_ROOT, "output")
# Schedule uploads: delete files older than this many hours (see views._cleanup).
STAFFING_UPLOAD_RETENTION_HOURS = 24
