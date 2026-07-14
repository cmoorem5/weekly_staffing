"""Django training codes settings page (add / remove / CSRF)."""

import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

from django.test import Client
from django.urls import reverse

from dashboard.models import TrainingCode as DjangoTrainingCode


class TrainingCodesSettingsViewTests(unittest.TestCase):
    def test_remove_forms_include_csrf_token(self):
        # Listing is read via the Django ORM ("staffing" alias), so the seed
        # row has to go through that same ORM for the view to see it.
        entry = DjangoTrainingCode.objects.using("staffing").create(
            code="ZZTESTCODE", created_at="2026-06-10T00:00:00Z"
        )
        try:
            c = Client(HTTP_HOST="localhost")
            resp = c.get(reverse("training_codes_settings"))
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"ZZTESTCODE", resp.content)
            forms = re.findall(
                r'<form method="post" class="d-inline".*?</form>',
                resp.content.decode("utf-8"),
                re.DOTALL,
            )
            self.assertGreater(len(forms), 0)
            for form in forms:
                self.assertIn("csrfmiddlewaretoken", form)
        finally:
            entry.delete(using="staffing")

    def test_add_then_remove_code(self):
        c = Client(HTTP_HOST="localhost")
        url = reverse("training_codes_settings")
        c.get(url)
        csrf = c.cookies["csrftoken"].value
        try:
            resp = c.post(
                url,
                {
                    "action": "add",
                    "code": "zztestcode2",
                    "csrfmiddlewaretoken": csrf,
                },
                follow=True,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"Added training code ZZTESTCODE2", resp.content)
            row = DjangoTrainingCode.objects.using("staffing").get(code="ZZTESTCODE2")

            resp = c.post(
                url,
                {
                    "action": "delete",
                    "code_id": str(row.id),
                    "csrfmiddlewaretoken": csrf,
                },
                follow=True,
            )
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"Removed training code", resp.content)
            self.assertFalse(
                DjangoTrainingCode.objects.using("staffing")
                .filter(code="ZZTESTCODE2")
                .exists()
            )
        finally:
            DjangoTrainingCode.objects.using("staffing").filter(
                code__in=["ZZTESTCODE2"]
            ).delete()


if __name__ == "__main__":
    unittest.main()
