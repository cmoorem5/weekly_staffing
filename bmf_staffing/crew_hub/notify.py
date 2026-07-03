"""In-app notification helpers."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from .models import Notification

MANAGER_GROUP = "Crew Hub Managers"


def notify(user, message: str, url: str = "") -> None:
    """Create one notification; silently skips missing users."""
    if user is None:
        return
    Notification.objects.create(user=user, message=message, url=url)


def notify_managers(message: str, url: str = "", exclude=None) -> int:
    """Notify everyone in the managers group (and superusers)."""
    users = (
        get_user_model()
        .objects.filter(is_active=True)
        .filter(groups__name=MANAGER_GROUP)
        .union(get_user_model().objects.filter(is_active=True, is_superuser=True))
    )
    count = 0
    for user in users:
        if exclude is not None and user.pk == exclude.pk:
            continue
        Notification.objects.create(user=user, message=message, url=url)
        count += 1
    return count


def assignment_owner(assignment):
    """The login linked to an assignment's person, if any."""
    person = getattr(assignment, "member", None) or getattr(assignment, "officer", None)
    return getattr(person, "user", None) if person else None
