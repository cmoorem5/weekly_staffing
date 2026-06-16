"""Tests for the Database backups page (list + one-click restore).

Validates the new Settings/Admin backups view: it lists archive/ snapshots,
restores a selected backup inside archive/ only (path-traversal safe), and
always takes a pre-restore safety backup first.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from django.test import Client, override_settings
from django.urls import reverse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bmf_staffing"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bmf_staffing.settings")

import django

django.setup()

from dashboard.views.admin_tools import (
    _human_size,
    _parse_backup_timestamp,
    _safe_archive_backup_path,
)


class BackupHelperTests(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(_human_size(0), "0 B")
        self.assertEqual(_human_size(2048), "2.0 KB")
        self.assertEqual(_human_size(5 * 1024 * 1024), "5.0 MB")

    def test_parse_timestamp(self):
        self.assertEqual(
            _parse_backup_timestamp("staffing_autobackup_20250115T030405Z.db"),
            "2025-01-15 03:04 UTC",
        )
        self.assertEqual(
            _parse_backup_timestamp("staffing_backup_20250115T030405Z_1.db"),
            "2025-01-15 03:04 UTC",
        )
        self.assertEqual(_parse_backup_timestamp("not_a_backup.db"), "")

    def test_safe_archive_path_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive"
            archive.mkdir()
            good = archive / "staffing_backup_20250101T000000Z.db"
            good.write_text("data")
            self.assertIsNotNone(_safe_archive_backup_path(archive, good.name))
            self.assertIsNone(
                _safe_archive_backup_path(archive, "../staffing.db")
            )
            self.assertIsNone(_safe_archive_backup_path(archive, "missing.db"))


class DatabaseBackupsViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db_path = root / "staffing.db"
        self.db_path.write_text("CURRENT")
        self.archive = root / "archive"
        self.archive.mkdir()
        self.backup = self.archive / "staffing_autobackup_20250115T030405Z.db"
        self.backup.write_text("RESTORED")
        self.client = Client(HTTP_HOST="localhost")

    def tearDown(self):
        self.tmp.cleanup()

    def _settings(self):
        return override_settings(STAFFING_DB_PATH=str(self.db_path))

    def test_list_renders_and_shows_backup(self):
        with self._settings():
            resp = self.client.get(reverse("database_backups"))
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn(self.backup.name, body)
        self.assertIn("Automatic", body)

    def test_restore_overwrites_db_and_makes_safety_backup(self):
        with self._settings():
            resp = self.client.post(
                reverse("database_backups"),
                {"backup_name": self.backup.name},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.db_path.read_text(), "RESTORED")
        # A pre-restore safety backup of the prior DB must now exist.
        safety = [
            p
            for p in self.archive.glob("staffing_backup_*.db")
            if p.read_text() == "CURRENT"
        ]
        self.assertTrue(safety, "expected a pre-restore safety backup of prior DB")

    def test_restore_rejects_unknown_file(self):
        with self._settings():
            resp = self.client.post(
                reverse("database_backups"),
                {"backup_name": "../staffing.db"},
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.db_path.read_text(), "CURRENT")

    def test_non_localhost_blocked(self):
        client = Client(HTTP_HOST="localhost", REMOTE_ADDR="10.0.0.5")
        with self._settings():
            resp = client.get(reverse("database_backups"))
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
