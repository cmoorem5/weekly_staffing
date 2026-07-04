"""Template context: unread notification count for the Crew Hub bell."""

from .models import Notification


def notifications(request):
    # The bell only renders on Crew Hub pages; skip the count query on the
    # rest of the site (dashboard pages, admin, ...).
    if not request.path.startswith("/hub/"):
        return {"crew_hub_unread": 0}
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"crew_hub_unread": 0}
    return {
        "crew_hub_unread": Notification.objects.filter(
            user=request.user, read=False
        ).count()
    }
