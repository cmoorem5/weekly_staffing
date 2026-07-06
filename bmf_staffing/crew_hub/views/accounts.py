"""Users & permissions page: create logins and assign permission levels."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .. import roles
from .helpers import USERS_DENIED_MSG, can_manage_users


def _may_edit(actor, target) -> tuple[bool, str]:
    """Guard rails for level / password / active changes."""
    if target.pk == actor.pk:
        return False, "You can't change your own account here."
    if target.is_superuser and not actor.is_superuser:
        return False, "Only a superuser can change another superuser."
    return True, ""


def _post_level(request, target) -> None:
    level = request.POST.get("level", "")
    if level not in roles.VALID_LEVELS:
        messages.error(request, "Pick a valid permission level.")
        return
    roles.set_level(target, level)
    messages.success(
        request,
        f"{target.get_username()} is now {roles.LEVEL_LABELS[level]}.",
    )


def _post_password(request, target) -> None:
    password = request.POST.get("password", "").strip()
    shown = ""
    if not password:
        password = roles.generate_temp_password()
        shown = f" Temporary password: {password}"
    target.set_password(password)
    target.save(update_fields=["password"])
    messages.success(request, f"Password reset for {target.get_username()}.{shown}")


def _post_toggle(request, target) -> None:
    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])
    state = "reactivated" if target.is_active else "deactivated"
    messages.success(request, f"{target.get_username()} {state}.")


def _post_create(request) -> None:
    user, temp_password, error = roles.create_login(
        request.POST.get("username", ""),
        email=request.POST.get("email", ""),
        first_name=request.POST.get("first_name", ""),
        last_name=request.POST.get("last_name", ""),
        password=request.POST.get("password", "").strip(),
        level=request.POST.get("level") or roles.LEVEL_MEMBER,
    )
    if error:
        messages.error(request, error)
        return
    shown = f" Temporary password: {temp_password}" if temp_password else ""
    messages.success(
        request,
        f"Created login “{user.get_username()}” "
        f"({roles.LEVEL_LABELS[roles.get_level(user)]}).{shown} "
        "Link it to a roster person from the Comm Center or Duty roster page.",
    )


@login_required
def user_admin(request):
    if not can_manage_users(request.user):
        messages.error(request, USERS_DENIED_MSG)
        return redirect("crew_hub:hub_home")

    User = get_user_model()
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            if request.POST.get("level") not in roles.VALID_LEVELS:
                messages.error(request, "Pick a valid permission level.")
                return redirect("crew_hub:user_admin")
            _post_create(request)
            return redirect("crew_hub:user_admin")
        target = User.objects.filter(pk=request.POST.get("pk") or None).first()
        if target is None:
            messages.error(request, "Unknown user.")
            return redirect("crew_hub:user_admin")
        allowed, why = _may_edit(request.user, target)
        if not allowed:
            messages.error(request, why)
        elif action == "level":
            _post_level(request, target)
        elif action == "password":
            _post_password(request, target)
        elif action == "toggle":
            _post_toggle(request, target)
        return redirect("crew_hub:user_admin")

    users = (
        User.objects.order_by("username")
        .select_related("comm_profile", "duty_profile")
        .prefetch_related("groups")
    )
    rows = []
    for user in users:
        linked = [
            profile.name
            for profile in (
                getattr(user, "comm_profile", None),
                getattr(user, "duty_profile", None),
            )
            if profile
        ]
        rows.append(
            {
                "user": user,
                "level": roles.get_level(user),
                "level_label": roles.LEVEL_LABELS[roles.get_level(user)],
                "linked": ", ".join(linked),
                "editable": _may_edit(request.user, user)[0],
            }
        )
    return render(
        request,
        "crew_hub/users.html",
        {"rows": rows, "level_choices": roles.LEVEL_CHOICES},
    )
