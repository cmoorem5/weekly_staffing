"""KPI tile status: wording, targets, and rendering on PDF and HTML tiles."""

import unittest

from staffing_tool import report_html as rh
from staffing_tool import report_style as style
from staffing_tool.db import session_scope
from staffing_tool.report_data import STATUS_COLORS, kpi_tile_statuses
from tests._temp_db import TempDbTestCase


class KpiTileStatusTests(TempDbTestCase):
    def setUp(self):
        self.make_temp_db()  # init_db seeds the default KPI thresholds

    def _statuses(self, values):
        with session_scope(self.db_path) as session:
            return kpi_tile_statuses(session, values)

    def test_spec_wording_with_target(self):
        got = self._statuses(
            {
                "Avg Staffing Rate": 0.92,  # yellow band 90-94.9%
                "OT Dependency": 0.05,  # green <= 8%
                "Shift Exception %": 0.40,  # red > 32%
            }
        )
        self.assertEqual(
            got["Avg Staffing Rate"], ("Monitor · ≥ 95%", STATUS_COLORS["Yellow"])
        )
        self.assertEqual(
            got["OT Dependency"], ("On target · ≤ 8%", STATUS_COLORS["Green"])
        )
        self.assertEqual(
            got["Shift Exception %"], ("Action needed · ≤ 25%", STATUS_COLORS["Red"])
        )

    def test_tiles_map_to_coverage_thresholds(self):
        got = self._statuses({"System RW %": 0.97, "Avg System GR %": 0.80})
        self.assertEqual(got["System RW %"][0], "On target · ≥ 95%")
        self.assertEqual(got["Avg System GR %"][0], "Action needed · ≥ 92%")

    def test_untargeted_tiles_get_no_status(self):
        got = self._statuses({"Training Events": 3.0, "Day Fill": 0.9})
        self.assertEqual(got, {})

    def test_never_uses_color_words(self):
        got = self._statuses({"Staffing Rate": 0.5, "OT Dependency": 0.1})
        for text, _color in got.values():
            for word in ("Green", "Yellow", "Red"):
                self.assertNotIn(word, text)


class KpiTileRenderingTests(unittest.TestCase):
    KPIS = [("Staffing Rate", "92.0%"), ("Training Events", "3")]
    STATUS = {"Staffing Rate": ("Monitor · ≥ 95%", "#B7791F")}

    def test_pdf_tiles_add_a_status_row_only_when_given(self):
        plain = style.kpi_row(self.KPIS)
        self.assertEqual(len(plain._cellvalues), 2)
        t = style.kpi_row(self.KPIS, self.STATUS)
        self.assertEqual(t._cellvalues[2], ["Monitor · ≥ 95%", ""])

    def test_html_tiles_show_status_and_a_colored_top_border(self):
        html = rh.kpi_strip(self.KPIS, self.STATUS)
        self.assertIn("Monitor · ≥ 95%", html)
        self.assertIn("border-top:4px solid #B7791F", html)
        self.assertNotIn("border-top", rh.kpi_strip(self.KPIS))


if __name__ == "__main__":
    unittest.main()
