"""
Django settings for BMF Staffing Dashboard.

Runs locally; uses existing staffing.db next to the Weekly_staffing folder.
"""

import os
import sys
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
    # Serves static files from any WSGI server (waitress in the desktop
    # deployment) — runserver's static handling only exists in dev.
    "whitenoise.middleware.WhiteNoiseMiddleware",
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
                "crew_hub.context_processors.notifications",
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

# Optional PostgreSQL for the default (Django/Crew Hub) database — set
# DJANGO_DB_ENGINE=postgresql plus the DJANGO_DB_* variables in .env.
# Requires `pip install "psycopg[binary]"`. The legacy `staffing` alias
# stays on SQLite either way (it is SQLAlchemy-owned).
if os.environ.get("DJANGO_DB_ENGINE", "").lower() in ("postgres", "postgresql"):
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DJANGO_DB_NAME", "crew_hub"),
        "USER": os.environ.get("DJANGO_DB_USER", "crew_hub"),
        "PASSWORD": os.environ.get("DJANGO_DB_PASSWORD", ""),
        "HOST": os.environ.get("DJANGO_DB_HOST", "localhost"),
        "PORT": os.environ.get("DJANGO_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }

DATABASE_ROUTERS = ["dashboard.db_router.StaffingDbRouter"]

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
# collectstatic target (gitignored); Update_Crew_Hub.bat runs collectstatic.
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Non-manifest compressed storage: keeps serving even if a stale
    # reference survives a partial collectstatic on the laptop deployment.
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
if DEBUG:
    # Serve straight from app static/ dirs in dev — no collectstatic needed.
    WHITENOISE_USE_FINDERS = True
    WHITENOISE_AUTOREFRESH = True
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

# Base weather strip on the Today board: "Base:ICAO" pairs, comma-separated.
# Stations are the nearest METAR-reporting airports (Mansfield's field does
# not report, so Norwood covers it). Set AOC_WEATHER_STATIONS= (empty) to
# turn the strip off entirely.
_default_weather = (
    "Bedford:KBED,Lawrence:KLWM,Manchester:KMHT,Mansfield:KOWD,Plymouth:KPYM"
)
CREW_HUB_WEATHER_STATIONS = [
    (pair.split(":", 1)[0].strip(), pair.split(":", 1)[1].strip().upper())
    for pair in os.environ.get("AOC_WEATHER_STATIONS", _default_weather).split(",")
    if ":" in pair and pair.split(":", 1)[0].strip() and pair.split(":", 1)[1].strip()
]
# Never fetch live weather inside the test suite (tests override the
# station list explicitly when they exercise the weather path).
if "test" in sys.argv:
    CREW_HUB_WEATHER_STATIONS = []

# --- Logging ------------------------------------------------------------
# Errors go to a rotating file in output/ (crew_hub_app.log — distinct from
# the crew_hub_server*.log console redirects written by the launcher) and,
# when DJANGO_ADMINS is set and DEBUG is off, to email via mail_admins.
ADMINS = [
    ("Crew Hub admin", addr.strip())
    for addr in os.environ.get("DJANGO_ADMINS", "").split(",")
    if addr.strip()
]
SERVER_EMAIL = DEFAULT_FROM_EMAIL

os.makedirs(STAFFING_OUTPUT_DIR, exist_ok=True)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
    },
    "formatters": {
        "verbose": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": os.path.join(STAFFING_OUTPUT_DIR, "crew_hub_app.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "delay": True,
            "formatter": "verbose",
        },
        "mail_admins": {
            "class": "django.utils.log.AdminEmailHandler",
            "level": "ERROR",
            "filters": ["require_debug_false"],
        },
    },
    "root": {"handlers": ["console", "file"], "level": "INFO"},
    "loggers": {
        "django.request": {
            "handlers": ["console", "file", "mail_admins"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}
