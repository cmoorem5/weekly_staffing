"""Security tests for the schedule import upload-path guard."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

from dashboard.views import helpers as view_helpers


class UploadedSchedulePathGuardTests(unittest.TestCase):
    """`_is_uploaded_schedule_path` must only accept real uploads in uploads/."""

    def _with_upload_dir(self, upload_dir: str):
        return patch.object(
            view_helpers, "_schedule_upload_dir", return_value=upload_dir
        )

    def test_accepts_valid_upload_in_uploads_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            valid = Path(tmp) / "schedule_upload_20260101T000000Z.xlsx"
            valid.write_bytes(b"not really excel but exists")
            with self._with_upload_dir(tmp):
                self.assertTrue(view_helpers._is_uploaded_schedule_path(str(valid)))

    def test_rejects_path_outside_uploads_dir(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as other,
        ):
            outside = Path(other) / "schedule_upload_20260101T000000Z.xlsx"
            outside.write_bytes(b"x")
            with self._with_upload_dir(tmp):
                self.assertFalse(view_helpers._is_uploaded_schedule_path(str(outside)))

    def test_rejects_traversal_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent_secret = Path(tmp).parent / "secret.xlsx"
            try:
                parent_secret.write_bytes(b"x")
                traversal = os.path.join(tmp, "..", "secret.xlsx")
                with self._with_upload_dir(tmp):
                    self.assertFalse(view_helpers._is_uploaded_schedule_path(traversal))
            finally:
                if parent_secret.exists():
                    parent_secret.unlink()

    def test_rejects_wrong_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "schedule_upload_20260101T000000Z.csv"
            bad.write_bytes(b"x")
            with self._with_upload_dir(tmp):
                self.assertFalse(view_helpers._is_uploaded_schedule_path(str(bad)))

    def test_rejects_wrong_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "evil.xlsx"
            bad.write_bytes(b"x")
            with self._with_upload_dir(tmp):
                self.assertFalse(view_helpers._is_uploaded_schedule_path(str(bad)))

    def test_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "schedule_upload_20260101T000000Z.xlsx"
            with self._with_upload_dir(tmp):
                self.assertFalse(view_helpers._is_uploaded_schedule_path(str(missing)))

    def test_rejects_empty_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self._with_upload_dir(tmp):
                self.assertFalse(view_helpers._is_uploaded_schedule_path(""))


if __name__ == "__main__":
    unittest.main()
