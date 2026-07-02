"""Create the 'Crew Hub Managers' group with schedule + reopen permissions.

Superusers pass all permission checks automatically; other users get
schedule-edit and report-reopen rights by joining this group (Django
admin → Groups, or the Users page).
"""

from django.db import migrations

GROUP_NAME = "Crew Hub Managers"
# (codename, human name) — must match DailyReport.Meta.permissions.
PERMISSIONS = [
    ("reopen_report", "Can reopen a submitted AOC daily report"),
    (
        "manage_schedules",
        "Can edit Crew Hub schedules, rotations, rosters, and vehicle statuses",
    ),
]


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    # On a fresh database the post_migrate permission sync has not run yet,
    # so create the permissions explicitly rather than looking them up.
    content_type, _ = ContentType.objects.get_or_create(
        app_label="crew_hub", model="dailyreport"
    )
    group, _ = Group.objects.get_or_create(name=GROUP_NAME)
    for codename, name in PERMISSIONS:
        permission, _ = Permission.objects.get_or_create(
            codename=codename,
            content_type=content_type,
            defaults={"name": name},
        )
        group.permissions.add(permission)


def remove_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name=GROUP_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("crew_hub", "0004_alter_dailyreport_options"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(create_group, remove_group),
    ]
