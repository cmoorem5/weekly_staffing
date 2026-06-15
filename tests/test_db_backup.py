"""Tests for the staffing.db backup utility."""

import tempfile
import unittest
from pathlib import Path

from staffing_tool.db_backup import (
    AUTO_BACKUP_PREFIX,
    create_db_backup,
)


class CreateDbBackupTests(unittest.TestCase):
    def test_returns_none_when_source_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "staffing.db"
            self.assertIsNone(create_db_backup(missing))

    def test_creates_timestamped_copy_in_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "staffing.db"
            db.write_bytes(b"sqlite-bytes")
            dest = create_db_backup(db)
            assert dest is not None
            self.assertTrue(dest.is_file())
            self.assertTrue(dest.name.startswith(AUTO_BACKUP_PREFIX))
            self.assertEqual(dest.parent.name, "archive")
            self.assertEqual(dest.read_bytes(), b"sqlite-bytes")

    def test_prune_keeps_only_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "staffing.db"
            db.write_bytes(b"x")
            archive = Path(tmp) / "archive"
            archive.mkdir()
            # Seed 5 older auto-backups with sortable timestamped names.
            for i in range(5):
                (archive / f"{AUTO_BACKUP_PREFIX}2026010{i}T000000Z.db").write_bytes(
                    b"old"
                )
            dest = create_db_backup(db, archive_dir=archive, keep=3)
            assert dest is not None
            remaining = sorted(archive.glob(f"{AUTO_BACKUP_PREFIX}*.db"))
            self.assertEqual(len(remaining), 3)
            # The just-created backup must survive pruning.
            self.assertIn(dest.name, [p.name for p in remaining])

    def test_manual_backups_not_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "staffing.db"
            db.write_bytes(b"x")
            archive = Path(tmp) / "archive"
            archive.mkdir()
            manual = archive / "staffing_backup_20200101T000000Z.db"
            manual.write_bytes(b"manual")
            for i in range(5):
                (archive / f"{AUTO_BACKUP_PREFIX}2026010{i}T000000Z.db").write_bytes(
                    b"old"
                )
            create_db_backup(db, archive_dir=archive, keep=1)
            self.assertTrue(manual.is_file())


if __name__ == "__main__":
    unittest.main()
