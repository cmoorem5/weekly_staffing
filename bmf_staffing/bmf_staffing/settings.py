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

# Comma-separated DJANGO_ALLOWED_HOSTS overrides the localhost-only default.
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]

# Fail fast in production if the placeholder secret was never replaced.
if not DEBUG and SECRET_KEY == "dev-key-change-in-production":
    raise RuntimeError(
        "DJANGO_SECRET_KEY must be set to a unique value when DJANGO_DEBUG is off."
    )

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

# Hardening that only applies once DEBUG is off (i.e. a real deployment).
# Left relaxed in local/dev so the loopback dashboard keeps working over HTTP.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
    # Honor X-Forwarded-Proto when running behind a TLS-terminating proxy.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "1").lower() in (
        "1",
        "true",
        "yes",
    )

# Staffing tool: Excel output (paths under Weekly_staffing/)
STAFFING_OUTPUT_DIR = os.path.join(WEEKLY_STAFFING_ROOT, "output")
# Schedule uploads: delete files older than this many hours (see views._cleanup).
# Keep uploaded schedule workbooks for one week (re-import / daily detail backfill).
STAFFING_UPLOAD_RETENTION_HOURS = 168
