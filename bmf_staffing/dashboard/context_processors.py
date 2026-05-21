"""Template context shared across dashboard pages."""

from .views.helpers import DB_PATH, OPS_PAGE_URL_NAMES, _ensure_db, staffing_db_snapshot


def staffing_ops_banner(request) -> dict[str, object]:
    """Last import / latest week banner on Operations pages only."""
    url_name = getattr(getattr(request, "resolver_match", None), "url_name", None)
    show = url_name in OPS_PAGE_URL_NAMES
    if not show:
        return {"show_ops_import_banner": False}

    _ensure_db()
    snap = staffing_db_snapshot(DB_PATH)
    return {
        "show_ops_import_banner": True,
        **snap,
    }
