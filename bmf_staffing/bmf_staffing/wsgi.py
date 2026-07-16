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
