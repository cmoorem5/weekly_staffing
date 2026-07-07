"""Template context: unread notification count for the sidebar badge."""

from .models import Notification


def notifications(request):
    # The badge renders in the app sidebar on every page, so count whenever
    # someone is signed in (admin pages excluded — they use their own base).
    if request.path.startswith("/admin/"):
        return {"crew_hub_unread": 0}
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"crew_hub_unread": 0}
    return {
        "crew_hub_unread": Notification.objects.filter(
            user=request.user, read=False
        ).count()
    }
