"""
Django settings for BMF Staffing Dashboard. Runs locally; uses existing staffing.db.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# Parent of project = Weekly_staffing (where staffing_tool and staffing.db live)
WEEKLY_STAFFING_ROOT = BASE_DIR.parent

SECRET_KEY = "dev-key-change-in-production"
DEBUG = True
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
            ],
        },
    },
]
WSGI_APPLICATION = "bmf_staffing.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/New_York"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Staffing tool: existing SQLite and output folder
STAFFING_DB_PATH = os.path.join(WEEKLY_STAFFING_ROOT, "staffing.db")
STAFFING_OUTPUT_DIR = os.path.join(WEEKLY_STAFFING_ROOT, "output")
# Schedule import temp files under uploads/; delete when older than this (hours).
STAFFING_UPLOAD_RETENTION_HOURS = 24
