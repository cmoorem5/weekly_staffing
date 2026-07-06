"""
Crew Hub permission levels.

Four levels, each mapped to a Django group created by migrations
(superusers always count as Admin regardless of groups):

* Admin    — everything, including the Users & permissions page.
* Manager  — edit schedules/rosters/vehicles, reopen reports, review time off.
* Reviewer — review and decide time-off requests; everything else read-only.
* Member   — view schedules, My Schedule, submit time-off requests.

A user holds exactly one level: ``set_level`` clears the other level groups.
"""

from __future__ import annotations

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

LEVEL_ADMIN = "admin"
LEVEL_MANAGER = "manager"
LEVEL_REVIEWER = "reviewer"
LEVEL_MEMBER = "member"

ADMIN_GROUP = "Crew Hub Admins"
MANAGER_GROUP = "Crew Hub Managers"
REVIEWER_GROUP = "Crew Hub Reviewers"

# Level -> group name; Member is the absence of any level group.
LEVEL_GROUPS = {
    LEVEL_ADMIN: ADMIN_GROUP,
    LEVEL_MANAGER: MANAGER_GROUP,
    LEVEL_REVIEWER: REVIEWER_GROUP,
}

LEVEL_CHOICES = [
    (LEVEL_MEMBER, "Member — view schedules and request time off"),
    (LEVEL_REVIEWER, "Reviewer — review and decide time-off requests"),
    (LEVEL_MANAGER, "Manager — edit schedules, reopen reports, review time off"),
    (LEVEL_ADMIN, "Admin — everything, including logins and permissions"),
]
LEVEL_LABELS = {code: label.split(" — ")[0] for code, label in LEVEL_CHOICES}
VALID_LEVELS = set(LEVEL_LABELS)


def get_level(user) -> str:
    """The user's current level (highest wins if groups were mixed by hand)."""
    if user.is_superuser:
        return LEVEL_ADMIN
    # .all() (not values_list) so a prefetch_related("groups") is honored.
    names = {group.name for group in user.groups.all()}
    for level in (LEVEL_ADMIN, LEVEL_MANAGER, LEVEL_REVIEWER):
        if LEVEL_GROUPS[level] in names:
            return level
    return LEVEL_MEMBER


def set_level(user, level: str) -> None:
    """Put the user in exactly the group for ``level`` (Member = none)."""
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown permission level: {level}")
    user.groups.remove(*Group.objects.filter(name__in=LEVEL_GROUPS.values()))
    if level != LEVEL_MEMBER:
        user.groups.add(Group.objects.get(name=LEVEL_GROUPS[level]))


def generate_temp_password() -> str:
    """A random one-time password to hand to the new person."""
    return secrets.token_urlsafe(9)


def create_login(
    username: str,
    *,
    email: str = "",
    first_name: str = "",
    last_name: str = "",
    password: str = "",
    level: str = LEVEL_MEMBER,
) -> tuple[object | None, str, str]:
    """Create a login at the given level.

    Returns ``(user, temp_password, error)``: on success ``error`` is empty
    and ``temp_password`` is the generated password ("" when the caller
    supplied one); on failure ``user`` is None and ``error`` explains why.
    """
    User = get_user_model()
    username = username.strip()
    if not username:
        return None, "", "Username is required."
    if User.objects.filter(username__iexact=username).exists():
        return None, "", f"A login named “{username}” already exists."
    temp_password = ""
    if not password:
        password = temp_password = generate_temp_password()
    user = User.objects.create_user(
        username,
        email=email.strip(),
        password=password,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
    )
    set_level(user, level)
    return user, temp_password, ""
