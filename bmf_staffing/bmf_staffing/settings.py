"""
Django settings for BMF Staffing Dashboard.

Runs locally; uses existing staffing.db next to the Weekly_staffing folder.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Parent of bmf_staffing/ = Weekly_staffing (staffing_tool + staffing.db)
WEEKLY_STAFFING_ROOT = BASE_DIR.parent

# Load a repo-root .env (gitignored) so secrets and email settings stay out
# of code. See .env.example for the supported variables.
try:
    from dotenv import load_dotenv

    load_dotenv(WEEKLY_STAFFING_ROOT / ".env")
except ImportError:  # dotenv optional: plain environment variables still work
    pass
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
    "crew_hub.apps.CrewHubConfig",
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

# Auto-backups of staffing.db before destructive writes (import / week delete).
# The most recent N are kept in archive/; manual backups are never pruned.
STAFFING_BACKUP_KEEP = 30

# --- Crew Hub (AOC Daily Report) ---------------------------------------
# Authentication: Django's built-in auth for now.
# TODO: Replace with Azure AD SSO (MSAL) once IT governance approves
# deployment; swap LOGIN_URL and the accounts/ URLs, keep @login_required.
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "crew_hub:hub_home"
LOGOUT_REDIRECT_URL = "login"

# Email: console backend by default so no mail leaves a dev machine.
# Configure SMTP entirely from environment variables for real sends.
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = os.environ.get("DJANGO_EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("DJANGO_EMAIL_USE_TLS", "true").lower() in (
    "1",
    "true",
    "yes",
)
DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "aoc-report@localhost")

# Comma-separated recipient list for the AOC Daily Report email.
CREW_HUB_REPORT_RECIPIENTS = [
    addr.strip()
    for addr in os.environ.get("AOC_REPORT_RECIPIENTS", "").split(",")
    if addr.strip()
]

# External Equipment OOS / MISS dashboard link shown on the report.
CREW_HUB_EQUIPMENT_DASHBOARD_URL = os.environ.get("AOC_EQUIPMENT_DASHBOARD_URL", "")
