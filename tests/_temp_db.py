"""Shared temp-``staffing.db`` fixture for the root test suite.

Tests that need an isolated legacy database used to hand-roll the same
teardown, and had drifted into several partial variants — some disposed
the engine but left the cached sessionmakers warm, some left the path
marked ready. Leftover state leaks between tests, so the teardown here
does the whole job and every temp-DB test uses it.

Django-touching tests still need their own ``sys.path`` /
``django.setup()`` preamble; this module deliberately imports nothing
from Django so it stays usable by the pure ``staffing_tool`` tests.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from staffing_tool.db import (
    _get_engine_cached,
    _sessionmaker_for_path,
    get_engine,
    init_db,
)


def dispose_temp_db(db_path: str) -> None:
    """Release every handle and cache entry pointing at ``db_path``.

    Disposes the engine, clears the two module-level LRU caches, and
    un-marks the path as initialized so a later test that reuses the same
    filename re-runs ``init_db`` instead of trusting a stale marker.
    """
    import staffing_tool.db as db_mod

    resolved = db_mod._resolve_db_path(db_path)
    get_engine(db_path).dispose()
    _get_engine_cached.cache_clear()
    _sessionmaker_for_path.cache_clear()
    db_mod._DB_READY_PATHS.discard(resolved)


class TempDbTestCase(unittest.TestCase):
    """TestCase owning a temporary ``staffing.db``.

    Call :meth:`make_temp_db` from ``setUp`` to get an initialized
    database; teardown disposes it and removes the directory. Subclasses
    that patch things can append to ``self._patchers`` and they are
    stopped first.
    """

    def make_temp_db(self, filename: str = "test.db") -> str:
        """Create and initialize a temp database; returns its path."""
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / filename)
        init_db(self.db_path)
        return self.db_path

    def tearDown(self) -> None:
        for patcher in getattr(self, "_patchers", ()):
            patcher.stop()
        if getattr(self, "db_path", None):
            dispose_temp_db(self.db_path)
        tmp = getattr(self, "tmp", None)
        if tmp is not None:
            tmp.cleanup()
