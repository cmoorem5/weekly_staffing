"""Permission levels: Admin / Manager / Reviewer / Member.

Adds the ``review_time_off`` and ``manage_users`` permissions and wires up
the level groups (see crew_hub/roles.py). The existing 'Crew Hub Managers'
group also gains ``review_time_off`` so managers keep approving time off.
"""

from django.db import migrations

# group name -> permission codenames (must match DailyReport.Meta.permissions)
GROUP_PERMISSIONS = {
    "Crew Hub Admins": [
        "manage_users",
        "manage_schedules",
        "reopen_report",
        "review_time_off",
    ],
    "Crew Hub Managers": ["manage_schedules", "reopen_report", "review_time_off"],
    "Crew Hub Reviewers": ["review_time_off"],
}

PERMISSION_NAMES = {
    "reopen_report": "Can reopen a submitted AOC daily report",
    "manage_schedules": (
        "Can edit Crew Hub schedules, rotations, rosters, and vehicle statuses"
    ),
    "review_time_off": "Can review and decide time-off requests",
    "manage_users": "Can manage Crew Hub logins and permission levels",
}


def create_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    # On a fresh database the post_migrate permission sync has not run yet,
    # so create the permissions explicitly rather than looking them up.
    content_type, _ = ContentType.objects.get_or_create(
        app_label="crew_hub", model="dailyreport"
    )
    for group_name, codenames in GROUP_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        for codename in codenames:
            permission, _ = Permission.objects.get_or_create(
                codename=codename,
                content_type=content_type,
                defaults={"name": PERMISSION_NAMES[codename]},
            )
            group.permissions.add(permission)


def remove_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    # Leave 'Crew Hub Managers' (owned by 0005); drop only the new groups.
    Group.objects.filter(name__in=["Crew Hub Admins", "Crew Hub Reviewers"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        (
            "crew_hub",
            "0006_commstaffmember_user_dutyofficer_user_notification_and_more",
        ),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="dailyreport",
            options={
                "ordering": ["-report_date"],
                "permissions": [
                    ("reopen_report", "Can reopen a submitted AOC daily report"),
                    (
                        "manage_schedules",
                        "Can edit Crew Hub schedules, rotations, rosters, and vehicle statuses",
                    ),
                    ("review_time_off", "Can review and decide time-off requests"),
                    (
                        "manage_users",
                        "Can manage Crew Hub logins and permission levels",
                    ),
                ],
            },
        ),
        migrations.RunPython(create_groups, remove_groups),
    ]
