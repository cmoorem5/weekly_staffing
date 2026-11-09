"""Where the live databases are, the one-time move, and the archive snapshot."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import relocate_databases, snapshot_databases
from scripts.relocate_databases import RelocateError, relocate
from staffing_tool import paths
from staffing_tool.db_backup import archive_dir_for

NOW = datetime(2026, 11, 9, 8, 30, 0)


def _make_db(path: Path, value: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


class _NoDotenv(unittest.TestCase):
    """Keep a developer's own repo-root .env out of these tests."""

    def setUp(self):
        patcher = patch.object(paths, "_load_dotenv", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)


class PathResolutionTests(_NoDotenv):
    def test_default_is_the_repo_folder(self):
        with patch.dict(os.environ, {paths.DATA_DIR_ENV: ""}):
            self.assertIsNone(paths.data_dir())
            self.assertEqual(paths.staffing_db_path(), ROOT / "staffing.db")
            self.assertEqual(
                paths.crew_hub_db_path(), ROOT / "bmf_staffing" / "db.sqlite3"
            )
            self.assertEqual(paths.default_cli_db_path(), "staffing.db")

    def test_data_dir_moves_both_databases(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {paths.DATA_DIR_ENV: tmp}):
                self.assertEqual(paths.staffing_db_path(), Path(tmp) / "staffing.db")
                self.assertEqual(paths.crew_hub_db_path(), Path(tmp) / "db.sqlite3")
                self.assertEqual(
                    paths.default_cli_db_path(), str(Path(tmp) / "staffing.db")
                )

    def test_environment_variables_in_the_value_are_expanded(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BMF_TEST_BASE": tmp, paths.DATA_DIR_ENV: "$BMF_TEST_BASE/data"}
            with patch.dict(os.environ, env):
                self.assertEqual(paths.data_dir(), Path(tmp) / "data")

    def test_unresolved_variable_is_an_error_not_an_empty_database(self):
        with patch.dict(os.environ, {paths.DATA_DIR_ENV: "$BMF_NOT_SET_ANYWHERE/data"}):
            os.environ.pop("BMF_NOT_SET_ANYWHERE", None)
            with self.assertRaises(RuntimeError):
                paths.staffing_db_path()

    def test_backups_of_the_moved_database_stay_in_the_repo_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {paths.DATA_DIR_ENV: tmp}):
                self.assertEqual(
                    archive_dir_for(Path(tmp) / "staffing.db"), paths.ARCHIVE_DIR
                )
                # Any other database (a test's, a --db copy) keeps its own.
                other = Path(tmp) / "elsewhere" / "staffing.db"
                self.assertEqual(archive_dir_for(other), other.parent / "archive")
        with patch.dict(os.environ, {paths.DATA_DIR_ENV: ""}):
            self.assertEqual(archive_dir_for(ROOT / "staffing.db"), ROOT / "archive")


class RelocateTests(_NoDotenv):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        self.data = Path(tmp.name) / "local" / "data"
        self.env = self.repo / ".env"
        self.staffing = self.repo / "staffing.db"
        self.hub = self.repo / "bmf_staffing" / "db.sqlite3"
        _make_db(self.staffing, "kpi")
        _make_db(self.hub, "hub")
        self.env.write_text("DJANGO_DEBUG=false\n", encoding="utf-8")

    def _go(self, **kw):
        kw.setdefault("check_server", False)
        return relocate(self.repo, str(self.data), self.env, now=NOW, **kw)

    def test_moves_both_and_keeps_the_originals_in_archive(self):
        self._go()
        self.assertEqual(_read(self.data / "staffing.db"), "kpi")
        self.assertEqual(_read(self.data / "db.sqlite3"), "hub")
        self.assertFalse(self.staffing.exists())
        self.assertFalse(self.hub.exists())
        archive = self.repo / "archive"
        self.assertEqual(
            _read(archive / "staffing_before_move_20261109_083000.db"), "kpi"
        )
        # Never a .db: the restore picker must not offer it as a staffing.db.
        kept_hub = archive / "crew_hub_before_move_20261109_083000.sqlite3"
        self.assertEqual(_read(kept_hub), "hub")
        env = self.env.read_text(encoding="utf-8")
        self.assertIn("DJANGO_DEBUG=false\n", env)
        self.assertIn(f"{paths.DATA_DIR_ENV}={self.data}\n", env)

    def test_rerun_does_nothing(self):
        self._go()
        before = sorted(p.name for p in (self.repo / "archive").iterdir())
        lines = self._go()
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            sorted(p.name for p in (self.repo / "archive").iterdir()), before
        )
        self.assertEqual(
            self.env.read_text(encoding="utf-8").count(paths.DATA_DIR_ENV), 1
        )

    def test_dry_run_changes_nothing(self):
        lines = self._go(dry_run=True)
        self.assertEqual(len(lines), 3)
        self.assertTrue(self.staffing.exists())
        self.assertFalse(self.data.exists())
        self.assertNotIn(paths.DATA_DIR_ENV, self.env.read_text(encoding="utf-8"))

    def _assert_untouched(self):
        self.assertEqual(_read(self.staffing), "kpi")
        self.assertEqual(_read(self.hub), "hub")
        self.assertNotIn(paths.DATA_DIR_ENV, self.env.read_text(encoding="utf-8"))
        self.assertFalse((self.repo / "archive").exists())

    def test_never_overwrites_a_database_already_in_the_data_folder(self):
        _make_db(self.data / "db.sqlite3", "other")
        with self.assertRaises(RelocateError):
            self._go()
        self._assert_untouched()
        self.assertEqual(_read(self.data / "db.sqlite3"), "other")
        self.assertFalse((self.data / "staffing.db").exists())

    def test_refuses_while_crew_hub_is_open(self):
        with patch.object(relocate_databases, "_server_running", lambda: True):
            with self.assertRaises(RelocateError):
                self._go(check_server=True)
        self._assert_untouched()

    def test_refuses_with_an_unfinished_write_beside_a_database(self):
        Path(str(self.hub) + "-journal").write_bytes(b"x")
        with self.assertRaises(RelocateError):
            self._go()
        self._assert_untouched()

    def test_refuses_a_corrupt_database(self):
        self.staffing.write_bytes(b"not a database at all" * 100)
        with self.assertRaises(RelocateError):
            self._go()
        self.assertFalse(self.data.exists())
        self.assertNotIn(paths.DATA_DIR_ENV, self.env.read_text(encoding="utf-8"))

    def test_refuses_a_data_folder_inside_the_program_folder(self):
        with self.assertRaises(RelocateError):
            relocate(self.repo, str(self.repo / "data"), self.env, check_server=False)
        self._assert_untouched()

    def test_fresh_install_with_no_databases_just_records_the_folder(self):
        self.staffing.unlink()
        self.hub.unlink()
        self._go()
        self.assertIn(paths.DATA_DIR_ENV, self.env.read_text(encoding="utf-8"))
        self.assertTrue(self.data.is_dir())

    def test_windows_line_endings_in_env_are_kept(self):
        self.env.write_bytes(b"DJANGO_DEBUG=false\r\n")
        self._go()
        raw = self.env.read_bytes()
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""))


class SnapshotTests(_NoDotenv):
    def test_snapshot_copies_both_and_replaces_the_previous_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, archive = Path(tmp) / "data", Path(tmp) / "archive"
            _make_db(data / "staffing.db", "kpi")
            _make_db(data / "db.sqlite3", "hub")
            with patch.dict(os.environ, {paths.DATA_DIR_ENV: str(data)}):
                written = snapshot_databases.snapshot_all(archive)
                self.assertEqual(len(written), 2)
                self.assertEqual(_read(archive / "staffing_latest.db"), "kpi")
                self.assertEqual(_read(archive / "crew_hub_latest.sqlite3"), "hub")
                conn = sqlite3.connect(data / "staffing.db")
                conn.execute("UPDATE t SET v = 'kpi2'")
                conn.commit()
                conn.close()
                snapshot_databases.snapshot_all(archive)
                self.assertEqual(_read(archive / "staffing_latest.db"), "kpi2")
                self.assertEqual(
                    sorted(p.name for p in archive.iterdir()),
                    ["crew_hub_latest.sqlite3", "staffing_latest.db"],
                )

    def test_missing_database_is_skipped_and_main_never_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {paths.DATA_DIR_ENV: tmp}):
                self.assertEqual(
                    snapshot_databases.snapshot_all(Path(tmp) / "archive"), []
                )
        with patch.dict(os.environ, {paths.DATA_DIR_ENV: "$BMF_NOT_SET_ANYWHERE/x"}):
            os.environ.pop("BMF_NOT_SET_ANYWHERE", None)
            self.assertEqual(snapshot_databases.main(), 0)

    def test_does_nothing_while_the_databases_are_in_the_program_folder(self):
        with patch.dict(os.environ, {paths.DATA_DIR_ENV: ""}):
            with patch.object(snapshot_databases, "snapshot_all") as snap:
                self.assertEqual(snapshot_databases.main(), 0)
                snap.assert_not_called()


if __name__ == "__main__":
    unittest.main()
