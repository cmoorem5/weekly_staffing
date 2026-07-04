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
    from django.db.models import Q

    users = get_user_model().objects.filter(
        Q(groups__name=MANAGER_GROUP) | Q(is_superuser=True), is_active=True
    )
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    notifications = [
        Notification(user=user, message=message, url=url) for user in users.distinct()
    ]
    Notification.objects.bulk_create(notifications)
    return len(notifications)


def assignment_owner(assignment):
    """The login linked to an assignment's person, if any."""
    person = getattr(assignment, "member", None) or getattr(assignment, "officer", None)
    return getattr(person, "user", None) if person else None
