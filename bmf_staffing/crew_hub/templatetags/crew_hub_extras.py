"""Template filters shared by the entry form, vehicle board, and email."""

from django import template

register = template.Library()

# Keyword-based status coloring from the reference form: OOS red, INIS
# orange, Primary green, anything else neutral gray.
STATUS_COLORS = {
    "oos": "#C12126",
    "inis": "#B85C00",
    "primary": "#0F6E56",
}
STATUS_DEFAULT_COLOR = "#5a6a7a"


def _status_key(value: str) -> str:
    upper = (value or "").upper()
    if "OOS" in upper:
        return "oos"
    if "INIS" in upper:
        return "inis"
    if "PRIMARY" in upper:
        return "primary"
    return ""


@register.filter
def vehicle_status_color(value: str) -> str:
    """Inline color for a vehicle status string (email-safe)."""
    return STATUS_COLORS.get(_status_key(value), STATUS_DEFAULT_COLOR)


@register.filter
def vehicle_status_weight(value: str) -> str:
    """Bold for exception states (OOS / INIS), normal otherwise."""
    return "bold" if _status_key(value) in ("oos", "inis") else "normal"


@register.filter
def vehicle_status_class(value: str) -> str:
    """CSS class hook for form inputs (st-oos / st-inis / st-primary)."""
    key = _status_key(value)
    return f"st-{key}" if key else ""


# Work-type accent colors (sick red, overtime orange, swap blue), shared by
# the hours report, my-schedule, and anywhere else that prints a work type.
WORK_TYPE_COLORS = {
    "sick": "#C12126",
    "overtime": "#b35e00",
    "swap": "#2A4492",
    "leave": "#6b3fa0",
}


@register.filter
def work_type_color(code: str) -> str:
    """Inline color for a work-type code; empty string for regular."""
    return WORK_TYPE_COLORS.get(code, "")


@register.filter
def get_item(mapping, key):
    """Dictionary lookup by variable key (e.g. calendar day cells)."""
    if mapping is None:
        return None
    return mapping.get(key)
