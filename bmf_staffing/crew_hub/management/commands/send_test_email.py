"""Send a test email to verify the SMTP (e.g. Outlook) settings in .env.

Usage:
    python manage.py send_test_email you@example.org

With the default console backend the message just prints to the terminal;
switch DJANGO_EMAIL_BACKEND to the smtp backend for a real send.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Send a test email to verify SMTP/Outlook settings from .env."

    def add_arguments(self, parser):
        parser.add_argument("recipient", help="Address to send the test to.")

    def handle(self, *args, **options):
        recipient = options["recipient"]
        backend = settings.EMAIL_BACKEND
        self.stdout.write(f"Backend: {backend}")
        if backend.endswith("smtp.EmailBackend"):
            self.stdout.write(
                f"SMTP:    {settings.EMAIL_HOST}:{settings.EMAIL_PORT} "
                f"(TLS {'on' if settings.EMAIL_USE_TLS else 'off'}) "
                f"as {settings.EMAIL_HOST_USER or '(no user)'}"
            )
        else:
            self.stdout.write(
                "Note: not the SMTP backend — nothing will actually be sent. "
                "Set DJANGO_EMAIL_BACKEND in .env for real sends."
            )
        self.stdout.write(f"From:    {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"To:      {recipient}")

        try:
            send_mail(
                subject="Crew Hub test email",
                message=(
                    "This is a test email from the BMF Crew Hub.\n\n"
                    f"Sent {timezone.now():%Y-%m-%d %H:%M %Z} via "
                    f"{settings.EMAIL_HOST or 'the configured backend'}.\n"
                    "If you are reading this, the email settings work."
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as exc:  # noqa: BLE001 - show the backend error
            raise CommandError(
                f"Send failed: {exc}\n"
                "Common Outlook fixes: use an app password (not your normal "
                "password), make sure DJANGO_DEFAULT_FROM_EMAIL matches "
                "DJANGO_EMAIL_HOST_USER, and confirm SMTP AUTH is allowed "
                "for the mailbox."
            ) from exc
        self.stdout.write(self.style.SUCCESS("Sent — check the inbox."))
