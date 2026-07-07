"""
Seed the Comm Center roster with the current Comm Tech names.

Usage:
    python manage.py seed_comm_techs

Adds each Comm Tech from the COMMS schedule as a CommStaffMember so they
show up in the seat pickers and on the rotations form. Safe to re-run:
existing names are left untouched (including their active flag and any
linked login).
"""

from django.core.management.base import BaseCommand

from crew_hub.models import CommStaffMember

# Roster rows from the COMMS schedule seat grid.
COMM_TECHS = [
    "Bilodeau, Henry",
    "Brennan, Ashley",
    "Castellana, Amber",
    "Donaldson, Keith",
    "Downs, Heidi",
    "Farrell, Mike",
    "Gardner, Keenan",
    "Holman, Ethan",
    "Newton, Eric",
    "Reilly, Katie",
    "Richard, Jeremy",
    "Savage, Kayla",
    "Sisson, Morgan",
    "Stone, Erika",
    "Treddin, Jack",
    "Venables, Elizabeth",
]

# Listed below the seat grid on the schedule (D-4 / Eval rows).
EXTRA_TECHS = {
    "Duggan, John": "Listed below the seat grid (D-4/Eval) on the schedule.",
    "Panciocco, Ken": "Listed below the seat grid on the schedule.",
}


class Command(BaseCommand):
    help = "Seed the Comm Center roster with the Comm Tech names."

    def handle(self, *args, **options):
        created = 0
        for name in COMM_TECHS:
            _, was_created = CommStaffMember.objects.get_or_create(name=name)
            created += was_created
        for name, note in EXTRA_TECHS.items():
            _, was_created = CommStaffMember.objects.get_or_create(
                name=name, defaults={"notes": note}
            )
            created += was_created
        total = len(COMM_TECHS) + len(EXTRA_TECHS)
        self.stdout.write(
            self.style.SUCCESS(
                f"Comm Tech roster seeded: {created} added, "
                f"{total - created} already present."
            )
        )
