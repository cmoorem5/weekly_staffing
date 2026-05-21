"""Tests for validation threshold helpers."""

import unittest
from types import SimpleNamespace

from staffing_tool.validation import (
    notes_required,
    notes_required_message,
    ot_action_ceiling,
    shift_exception_monitor_ceiling,
    staffing_action_floor,
)


def _threshold(**kwargs):
    defaults = {
        "metric_name": "Test",
        "green_min": 0.0,
        "green_max": 0.25,
        "yellow_min": 0.251,
        "yellow_max": 0.32,
        "red_min": 0.32,
        "red_max": 1.0,
        "higher_is_better": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class ValidationThresholdTests(unittest.TestCase):
    def test_defaults_without_threshold_rows(self):
        self.assertEqual(staffing_action_floor(None), 0.90)
        self.assertEqual(ot_action_ceiling(None), 0.12)
        self.assertEqual(shift_exception_monitor_ceiling(None), 0.25)

    def test_reads_db_thresholds(self):
        thresholds = {
            "Staffing Rate": _threshold(
                metric_name="Staffing Rate",
                green_min=0.95,
                yellow_min=0.88,
                yellow_max=0.94,
            ),
            "OT Dependency": _threshold(
                metric_name="OT Dependency",
                green_max=0.08,
                yellow_max=0.15,
            ),
        }
        self.assertEqual(staffing_action_floor(thresholds), 0.88)
        self.assertEqual(ot_action_ceiling(thresholds), 0.15)

    def test_notes_required_uses_thresholds(self):
        thresholds = {
            "Staffing Rate": _threshold(
                metric_name="Staffing Rate", yellow_min=0.88
            ),
            "OT Dependency": _threshold(
                metric_name="OT Dependency", yellow_max=0.15
            ),
        }
        self.assertTrue(
            notes_required(0.87, 0.05, 70, thresholds=thresholds)
        )
        self.assertTrue(
            notes_required(0.95, 0.16, 70, thresholds=thresholds)
        )
        self.assertFalse(
            notes_required(0.95, 0.10, 70, thresholds=thresholds)
        )

    def test_notes_required_message_includes_percentages(self):
        msg = notes_required_message(None)
        self.assertIn("90%", msg)
        self.assertIn("12%", msg)


if __name__ == "__main__":
    unittest.main()
