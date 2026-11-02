import os
import sys

# Repo root on sys.path so staffing_tool imports (mirrors manage.py) — the
# WSGI server (waitress) loads this module directly, bypassing manage.py.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from django.core.wsgi import get_wsgi_application  # noqa: E402

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")
application = get_wsgi_application()

# Warm-up. waitress only starts listening once this module has imported, and
# the launcher opens the window the moment the port answers, so doing the lazy
# first-request work here means the first page isn't the one that pays for it.
# Everything is best-effort: a failure here must never stop the server.
try:
    from django.urls import get_resolver

    get_resolver().url_patterns  # imports every view module (openpyxl, SQLAlchemy)

    from crew_hub import weather

    weather.prefetch()  # background thread; the Today board no longer waits on it
except Exception:  # noqa: BLE001
    import logging

    logging.getLogger(__name__).warning("Startup warm-up failed", exc_info=True)
