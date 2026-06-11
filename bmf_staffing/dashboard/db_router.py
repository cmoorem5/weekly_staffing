"""Route staffing.db mirror models; never migrate that file with Django."""

_STAFFING_MIRROR_MODELS = frozenset({"ManagerRosterLastName", "StaffRosterEntry"})


class StaffingDbRouter:
    """Send dashboard mirror models to the ``staffing`` database alias."""

    def db_for_read(self, model, **hints):
        if model.__name__ in _STAFFING_MIRROR_MODELS:
            return "staffing"
        return None

    def db_for_write(self, model, **hints):
        if model.__name__ in _STAFFING_MIRROR_MODELS:
            return "staffing"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "staffing":
            return False
        if app_label == "dashboard" and model_name in {
            "managerrosterlastname",
            "staffrosterentry",
        }:
            return False
        return None
