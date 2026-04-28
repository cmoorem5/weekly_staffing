"""Route ``ManagerRosterLastName`` to staffing.db; never migrate that file with Django."""


class StaffingDbRouter:
    """Send ``dashboard.ManagerRosterLastName`` ORM traffic to the ``staffing`` alias."""

    def db_for_read(self, model, **hints):
        if model.__name__ == "ManagerRosterLastName":
            return "staffing"
        return None

    def db_for_write(self, model, **hints):
        if model.__name__ == "ManagerRosterLastName":
            return "staffing"
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == "staffing":
            return False
        if app_label == "dashboard" and model_name == "managerrosterlastname":
            return False
        return None
