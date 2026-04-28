"""Report generator config checks (stdlib unittest — no pytest dependency)."""

import unittest

from staffing_tool.models import BaseConfig
from staffing_tool.report import (
    RW_SYSTEM_WEEKLY_DENOMINATOR,
    _assert_rw_config_rw_cap_56,
)


class TestRwConfigValidation(unittest.TestCase):
    def test_passes_when_sum_matches_denominator(self) -> None:
        bases = [
            BaseConfig(base_name="A", rw_total_unit_days=28, gr_total_unit_days=0),
            BaseConfig(base_name="B", rw_total_unit_days=28, gr_total_unit_days=0),
        ]
        _assert_rw_config_rw_cap_56(bases)

    def test_raises_when_sum_wrong(self) -> None:
        bases = [
            BaseConfig(base_name="A", rw_total_unit_days=55, gr_total_unit_days=0),
        ]
        with self.assertRaises(ValueError) as ctx:
            _assert_rw_config_rw_cap_56(bases)
        self.assertIn(str(RW_SYSTEM_WEEKLY_DENOMINATOR), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
