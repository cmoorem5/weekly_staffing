"""Tests for RAG threshold evaluation and direction arrows."""

import unittest
from types import SimpleNamespace

from staffing_tool.rag import (
    compare_direction,
    direction_for_metric,
    evaluate_rag,
)


def _threshold(**kwargs):
    defaults = {
        "green_min": None,
        "green_max": None,
        "yellow_min": None,
        "yellow_max": None,
        "red_min": None,
        "red_max": None,
        "higher_is_better": 0,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class EvaluateRagTests(unittest.TestCase):
    def test_value_in_green_range_is_green(self):
        t = _threshold(green_min=90, green_max=100)
        self.assertEqual(evaluate_rag(95, t), "Green")

    def test_value_in_yellow_range_is_yellow(self):
        t = _threshold(green_min=90, green_max=100, yellow_min=80, yellow_max=89.99)
        self.assertEqual(evaluate_rag(85, t), "Yellow")

    def test_value_in_red_range_is_red(self):
        t = _threshold(
            green_min=90,
            green_max=100,
            yellow_min=80,
            yellow_max=90,
            red_min=0,
            red_max=79.99,
        )
        self.assertEqual(evaluate_rag(50, t), "Red")

    def test_green_checked_before_yellow_on_overlap(self):
        t = _threshold(green_min=90, green_max=100, yellow_min=85, yellow_max=95)
        # 92 falls in both; green wins because it is checked first.
        self.assertEqual(evaluate_rag(92, t), "Green")

    def test_higher_is_better_above_green_floor(self):
        t = _threshold(green_min=90, higher_is_better=1)
        self.assertEqual(evaluate_rag(150, t), "Green")

    def test_lower_is_better_below_green_ceiling(self):
        t = _threshold(green_max=5, higher_is_better=0)
        self.assertEqual(evaluate_rag(1, t), "Green")

    def test_unbounded_yellow_range_catches_everything(self):
        # Documents existing behavior: an undefined yellow range [None, None]
        # matches any value, so anything not green falls to yellow.
        t = _threshold(green_min=90, green_max=100, higher_is_better=1)
        self.assertEqual(evaluate_rag(10, t), "Yellow")

    def test_below_all_defined_ranges_is_red(self):
        t = _threshold(
            green_min=90,
            green_max=100,
            yellow_min=80,
            yellow_max=89,
            red_min=70,
            red_max=79,
            higher_is_better=1,
        )
        self.assertEqual(evaluate_rag(10, t), "Red")


class DirectionTests(unittest.TestCase):
    def test_compare_direction_basic(self):
        self.assertEqual(compare_direction(5, 3), "↑")
        self.assertEqual(compare_direction(3, 5), "↓")
        self.assertEqual(compare_direction(5, 5), "→")
        self.assertEqual(compare_direction(5, None), "→")

    def test_higher_is_better_metric_not_flipped(self):
        # Staffing rate: up is genuinely up.
        self.assertEqual(direction_for_metric("Staffing Rate", 95, 90), "↑")

    def test_lower_is_better_metric_is_flipped(self):
        # OT Dependency rising is worse, so the arrow flips to ↓.
        self.assertEqual(direction_for_metric("OT Dependency", 20, 10), "↓")
        self.assertEqual(direction_for_metric("OT Dependency", 10, 20), "↑")
        self.assertEqual(direction_for_metric("OT Dependency", 10, 10), "→")


if __name__ == "__main__":
    unittest.main()
