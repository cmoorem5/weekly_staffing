"""scripts/secure_env.py: the desktop .env gets a real key and DEBUG off, once."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.secure_env import harden, main

EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def _values(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            out[name.strip()] = value.strip()
    return out


class HardenTests(unittest.TestCase):
    def test_fresh_copy_of_the_example_gets_a_key_and_debug_off(self):
        new, changes = harden(EXAMPLE)
        values = _values(new)
        self.assertNotEqual(
            values["DJANGO_SECRET_KEY"], "change-me-to-a-long-random-string"
        )
        self.assertGreaterEqual(len(values["DJANGO_SECRET_KEY"]), 50)
        self.assertEqual(values["DJANGO_DEBUG"], "false")
        self.assertEqual(values["DJANGO_LOOPBACK_HTTP"], "true")
        self.assertEqual(len(changes), 2)

    def test_rerun_changes_nothing(self):
        once, _ = harden(EXAMPLE)
        twice, changes = harden(once)
        self.assertEqual(once, twice)
        self.assertEqual(changes, [])

    def test_debug_turned_back_on_by_hand_sticks(self):
        once, _ = harden(EXAMPLE)
        edited = once.replace("DJANGO_DEBUG=false", "DJANGO_DEBUG=true")
        again, changes = harden(edited)
        self.assertEqual(again, edited)
        self.assertEqual(changes, [])

    def test_a_real_key_is_never_replaced(self):
        text = "DJANGO_SECRET_KEY=my-own-long-key\nDJANGO_DEBUG=true\n"
        new, _ = harden(text)
        self.assertEqual(_values(new)["DJANGO_SECRET_KEY"], "my-own-long-key")

    def test_missing_key_is_added(self):
        new, _ = harden("DJANGO_DEBUG=true\n")
        self.assertIn("DJANGO_SECRET_KEY", _values(new))

    def test_networked_install_keeps_its_debug_setting(self):
        text = (
            "DJANGO_SECRET_KEY=my-own-long-key\n"
            "DJANGO_DEBUG=false\n"
            "DJANGO_ALLOWED_HOSTS=staffing.example.org\n"
        )
        new, changes = harden(text)
        self.assertEqual(new, text)
        self.assertNotIn("DJANGO_LOOPBACK_HTTP", _values(new))
        self.assertEqual(len(changes), 1)

    def test_other_lines_and_windows_line_endings_are_kept(self):
        text = EXAMPLE.replace("\n", "\r\n")
        new, _ = harden(text)
        self.assertNotIn("\n", new.replace("\r\n", ""))
        kept = [
            line
            for line in text.splitlines()
            if not line.startswith(("DJANGO_SECRET_KEY=", "DJANGO_DEBUG="))
        ]
        for line in kept:
            self.assertIn(line, new.splitlines())

    def test_main_writes_the_file_and_dry_run_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(EXAMPLE, encoding="utf-8")
            self.assertEqual(main(["--env", str(path), "--dry-run"]), 0)
            self.assertEqual(path.read_text(encoding="utf-8"), EXAMPLE)
            self.assertEqual(main(["--env", str(path)]), 0)
            self.assertEqual(
                _values(path.read_text(encoding="utf-8"))["DJANGO_DEBUG"], "false"
            )
            self.assertEqual(main(["--env", str(Path(tmp) / "missing")]), 0)


class SettingsModeTests(unittest.TestCase):
    """Settings are read at import, so each mode loads in its own process."""

    def _settings(self, **env: str) -> subprocess.CompletedProcess:
        code = (
            "import json, sys\n"
            "sys.argv = ['x']\n"
            "from bmf_staffing import settings as s\n"
            "print(json.dumps({k: getattr(s, k, None) for k in ("
            "'DEBUG','ALLOWED_HOSTS','SESSION_COOKIE_SECURE','CSRF_COOKIE_SECURE',"
            "'SECURE_SSL_REDIRECT','SECURE_HSTS_SECONDS','X_FRAME_OPTIONS')}))\n"
        )
        clean = {
            k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "AOC_"))
        }
        # An empty value still counts as set, so a developer's own .env at the
        # repo root (python-dotenv never overrides) cannot leak into the test.
        base = dict.fromkeys(
            (
                "DJANGO_SECRET_KEY",
                "DJANGO_DEBUG",
                "DJANGO_LOOPBACK_HTTP",
                "DJANGO_ALLOWED_HOSTS",
                "DJANGO_SSL_REDIRECT",
            ),
            "",
        )
        base["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1"
        base["DJANGO_SSL_REDIRECT"] = "1"
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT / "bmf_staffing",
            env={**clean, **base, **env, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
        )

    def _loaded(self, **env: str) -> dict:
        import json

        proc = self._settings(**env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_desktop_mode_is_debug_off_without_https_only_settings(self):
        s = self._loaded(
            DJANGO_DEBUG="false",
            DJANGO_LOOPBACK_HTTP="true",
            DJANGO_SECRET_KEY="k" * 50,
        )
        self.assertFalse(s["DEBUG"])
        self.assertFalse(s["SESSION_COOKIE_SECURE"])
        self.assertFalse(s["CSRF_COOKIE_SECURE"])
        self.assertFalse(s["SECURE_SSL_REDIRECT"])
        self.assertFalse(s["SECURE_HSTS_SECONDS"])
        self.assertEqual(s["X_FRAME_OPTIONS"], "DENY")

    def test_desktop_mode_only_answers_on_loopback(self):
        s = self._loaded(
            DJANGO_DEBUG="false",
            DJANGO_LOOPBACK_HTTP="true",
            DJANGO_SECRET_KEY="k" * 50,
            DJANGO_ALLOWED_HOSTS="staffing.example.org",
        )
        self.assertEqual(s["ALLOWED_HOSTS"], ["localhost", "127.0.0.1", "[::1]"])

    def test_networked_production_keeps_the_https_settings(self):
        s = self._loaded(DJANGO_DEBUG="false", DJANGO_SECRET_KEY="k" * 50)
        self.assertTrue(s["SESSION_COOKIE_SECURE"])
        self.assertTrue(s["CSRF_COOKIE_SECURE"])
        self.assertTrue(s["SECURE_SSL_REDIRECT"])
        self.assertEqual(s["SECURE_HSTS_SECONDS"], 31536000)

    def test_sample_key_from_the_example_file_is_refused_with_debug_off(self):
        for key in (
            "change-me-to-a-long-random-string",
            "dev-key-change-in-production",
        ):
            proc = self._settings(DJANGO_DEBUG="false", DJANGO_SECRET_KEY=key)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("DJANGO_SECRET_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main()
