"""Characterization tests for helpers shared by the weekly and quarterly reports.

The two PDF builders grew a set of copy-pasted helpers. These tests pin the
rendered result — cell values, resolved cell styling, column widths, and
chart series — so the shared implementations they were folded into stay
byte-for-byte equivalent to what each builder produced on its own.

Styling is asserted through reportlab's *resolved* cell styles rather than
the TableStyle commands, so an equivalent-but-rewritten command list still
passes while a genuine visual change fails.
"""

import unittest

from staffing_tool import quarterly_pdf_report as Q
from staffing_tool import report_html
from staffing_tool import weekly_pdf_report as W

LEAVE_BREAKDOWN = [("AT", 10), ("LT", 6), ("SICK", 3), ("JURY", 1)]
BASE_COVERAGE = [
    ("Bedford", "20", "95.2%", "14", "100.0%"),
    ("Plymouth", "14", "88.0%", "7", "50.0%"),
]
TREND = [("2025-12-07", 0.91, 0.12, 0.08), ("2025-12-14", 0.87, 0.15, 0.10)]

# Cell attributes that decide how a cell actually looks on the page.
CELL_ATTRS = (
    "fontname",
    "fontsize",
    "alignment",
    "color",
    "background",
    "leftPadding",
    "rightPadding",
    "topPadding",
    "bottomPadding",
    "valign",
)


def cell_styles(table):
    """Resolved per-cell styling, as reportlab will paint it."""
    return [
        [{a: str(getattr(cs, a, None)) for a in CELL_ATTRS} for cs in row]
        for row in table._cellStyles
    ]


def col_widths(table):
    return [round(float(w), 4) for w in table._colWidths]


def weekly_ctx():
    return W.WeeklyReportContext(
        week_start="2025-12-07",
        week_of="Dec 7",
        week_dates="Dec 7-13",
        prepared_date="Dec 15, 2025",
        kpi_data=[],
        daily_data=[],
        daily_totals=("", "", "", ""),
        base_coverage=BASE_COVERAGE,
        leave_breakdown=LEAVE_BREAKDOWN,
        ot_by_role=[],
        ot_total_day=0,
        ot_total_night=0,
        exception_by_role=[],
        exception_col_totals=(0, 0, 0, 0, 0, 0),
        trend_data=TREND,
    )


def quarterly_ctx():
    return Q.QuarterlyReportContext(
        fy_label_year=2026,
        quarter=2,
        period="Q2",
        dates="Oct-Dec",
        weeks_count=13,
        prepared_date="Jan 5, 2026",
        kpi_data=[],
        weekly_trend=TREND,
        leave_breakdown=LEAVE_BREAKDOWN,
        period_volumes=[],
        period_vol_total=("",) * 7,
        base_coverage=BASE_COVERAGE,
        weekly_detail=[],
    )


class LeaveHelperTests(unittest.TestCase):
    def test_leave_rows_percentages_and_total(self):
        rows, total = W._leave_rows(weekly_ctx())
        self.assertEqual(total, 20)
        self.assertEqual(
            [(c, n, p) for c, n, p in rows],
            [
                ("AT", 10, "50.0%"),
                ("LT", 6, "30.0%"),
                ("SICK", 3, "15.0%"),
                ("JURY", 1, "5.0%"),
            ],
        )

    def test_leave_rows_handles_an_empty_breakdown(self):
        ctx = weekly_ctx()
        ctx.leave_breakdown = []
        self.assertEqual(W._leave_rows(ctx), ([], 0))

    def test_leave_top2_picks_the_two_largest(self):
        self.assertEqual(W._leave_top2(weekly_ctx()), {"AT", "LT"})

    def test_both_builders_agree_on_leave_rows_and_top2(self):
        w_rows, w_total = W._leave_rows(weekly_ctx())
        q_rows, q_total = Q._leave_rows(quarterly_ctx())
        self.assertEqual(w_rows, q_rows)
        self.assertEqual(w_total, q_total)
        self.assertEqual(W._leave_top2(weekly_ctx()), Q._leave_top2(quarterly_ctx()))

    def test_pct_and_short_label(self):
        self.assertEqual(W._pct(0.9123), "91.2%")
        self.assertEqual(W._pct(0), "0.0%")
        self.assertEqual(W._pct(1), "100.0%")
        self.assertEqual(W._short_label("2025-12-07"), "Dec 7")
        self.assertEqual(W._short_label("2026-01-19"), "Jan 19")
        self.assertEqual(W._pct(0.9123), Q._pct(0.9123))
        self.assertEqual(W._short_label("2025-12-07"), Q._short_label("2025-12-07"))


class BaseCoverageTableTests(unittest.TestCase):
    def setUp(self):
        self.weekly = W._base_coverage_table(weekly_ctx())
        self.quarterly = Q._base_coverage_table(quarterly_ctx())

    def test_headers_and_rows(self):
        self.assertEqual(
            self.weekly._cellvalues,
            [
                ["Base", "RW Shifts", "RW Avail %", "GR Shifts", "GR Avail %"],
                ["Bedford", "20", "95.2%", "14", "100.0%"],
                ["Plymouth", "14", "88.0%", "7", "50.0%"],
            ],
        )

    def test_both_builders_share_content_and_styling(self):
        self.assertEqual(self.weekly._cellvalues, self.quarterly._cellvalues)
        self.assertEqual(cell_styles(self.weekly), cell_styles(self.quarterly))

    def test_each_report_keeps_its_own_column_widths(self):
        """The one intentional difference between the two copies."""
        self.assertEqual(col_widths(self.weekly), [108.0, 90.0, 90.0, 90.0, 162.0])
        self.assertEqual(col_widths(self.quarterly), [129.6, 93.6, 93.6, 93.6, 129.6])


class TrendFigureTests(unittest.TestCase):
    def _current(self, ax):
        """The current-period series: the marker line (prior/target have none)."""
        return next(ln for ln in ax.get_lines() if ln.get_marker() == "o")

    def test_one_panel_per_kpi_with_its_own_series(self):
        fig = W._build_trend_fig(weekly_ctx())
        try:
            staffing, ot, exc = fig.axes
            for ax, expected in (
                (staffing, [0.91, 0.87]),
                (ot, [0.12, 0.15]),
                (exc, [0.08, 0.10]),
            ):
                self.assertEqual(
                    [round(float(v), 4) for v in self._current(ax).get_ydata()],
                    expected,
                )
            self.assertEqual(
                [t.get_text() for t in staffing.get_xticklabels()],
                ["2025-12-07", "2025-12-14"],
            )
        finally:
            _close(fig)

    def test_three_panels_and_never_a_second_y_axis(self):
        fig = W._build_trend_fig(weekly_ctx())
        try:
            # One axis per KPI; a twinx axis would add a fourth.
            self.assertEqual(len(fig.axes), 3)
            self.assertEqual(
                [ax.get_title(loc="left") for ax in fig.axes],
                ["Staffing rate", "OT dependency", "Shift exception %"],
            )
        finally:
            _close(fig)

    def test_target_named_in_panel_and_drawn_when_on_scale(self):
        ctx = weekly_ctx()
        ctx.trend_data = [("Dec 7", 93.0, 9.0, 4.0), ("Dec 14", 96.0, 7.0, 5.0)]
        ctx.trend_targets = {
            "Staffing Rate": 0.95,
            "OT Dependency": 0.08,
            "Shift Exception %": 0.25,
        }
        fig = W._build_trend_fig(ctx)
        try:
            staffing, ot, exc = fig.axes

            def texts(ax):
                return [t.get_text() for t in ax.texts]

            self.assertIn("target ≥ 95%", texts(staffing))
            self.assertIn("target ≤ 8%", texts(ot))
            self.assertIn("target ≤ 25%", texts(exc))
            labels = [[ln.get_label() for ln in ax.get_lines()] for ax in fig.axes]
            self.assertIn("Staffing Rate target", labels[0])
            self.assertIn("OT Dependency target", labels[1])
            # 25% is far above a 4-5% series: named, not drawn, so the
            # exception panel stays zoomed on the data.
            self.assertNotIn("Shift Exception % target", labels[2])
            # Latest value printed at the end of each line.
            self.assertIn("96.0%", texts(staffing))
            self.assertIn("5.0%", texts(exc))
        finally:
            _close(fig)

    def test_prior_period_drawn_behind_with_a_legend(self):
        from staffing_tool import report_style as style

        trend = [("Oct", 92.0, 9.0, 4.0), ("Nov", 94.0, 8.0, 5.0)]
        prior = [("Oct", 90.0, 10.0, 3.0), ("Nov", float("nan"), 9.5, 3.5)]
        fig = style.trend_fig(
            trend, prior=prior, current_label="FY2026", prior_label="FY2025"
        )
        try:
            gray = [
                ln for ln in fig.axes[0].get_lines() if ln.get_color() == style.C_PRIOR
            ]
            self.assertEqual(len(gray), 1)
            self.assertEqual(
                [t.get_text() for t in fig.legends[0].texts], ["FY2026", "FY2025"]
            )
        finally:
            _close(fig)

    def test_single_series_has_no_legend(self):
        fig = W._build_trend_fig(weekly_ctx())
        try:
            self.assertEqual(fig.legends, [])
        finally:
            _close(fig)

    def test_both_reports_plot_the_same_series_from_the_same_data(self):
        weekly, quarterly = (
            W._build_trend_fig(weekly_ctx()),
            Q._build_trend_fig(quarterly_ctx()),
        )
        try:
            self.assertEqual(
                [[list(ln.get_ydata()) for ln in ax.get_lines()] for ax in weekly.axes],
                [
                    [list(ln.get_ydata()) for ln in ax.get_lines()]
                    for ax in quarterly.axes
                ],
            )
            self.assertEqual(
                [round(v, 3) for v in weekly.get_size_inches()], [7.5, 2.5]
            )
        finally:
            _close(weekly, quarterly)

    def test_chart_to_image_keeps_aspect_ratio(self):
        from staffing_tool import report_style as style

        fig = W._build_trend_fig(weekly_ctx())
        img = style.chart_to_image(fig, style.USABLE_W)
        # 7.5 x 2.5 in figure (+/- tight-bbox cropping): drawn height must
        # follow the aspect, not the PNG's pixel height.
        ratio = img.drawHeight / img.drawWidth
        self.assertAlmostEqual(ratio, 2.5 / 7.5, delta=0.08)

    def test_exception_bars_sorted_largest_first_in_one_color(self):
        weekly, quarterly = (
            W._build_exception_bar_fig(weekly_ctx()),
            Q._build_exception_bar_fig(quarterly_ctx()),
        )
        try:
            bars = weekly.axes[0].patches
            self.assertEqual([p.get_width() for p in bars], [10, 6, 3, 1])
            self.assertEqual(len({p.get_facecolor() for p in bars}), 1)
            self.assertEqual(
                [t.get_text() for t in weekly.axes[0].get_yticklabels()],
                ["AT", "LT", "SICK", "JURY"],
            )
            self.assertIn("10 (50%)", [t.get_text() for t in weekly.axes[0].texts])
            self.assertEqual(
                [p.get_width() for p in bars],
                [p.get_width() for p in quarterly.axes[0].patches],
            )
        finally:
            _close(weekly, quarterly)


class HtmlDataTableTests(unittest.TestCase):
    HEADERS = ["Base", "Shifts"]
    ROWS = [["Bedford", "20"], ["Plymouth", "14"], ["Total", "34"]]

    def test_weekly_copy_matches_the_shared_html_table(self):
        for right_cols, total_row in (
            (None, False),
            ({1}, True),
            (set(), True),
        ):
            with self.subTest(right_cols=right_cols, total_row=total_row):
                shared = report_html.data_table(
                    self.HEADERS,
                    self.ROWS,
                    right_cols=right_cols,
                    total_row=total_row,
                )
                weekly = W._html_data_table(
                    self.HEADERS,
                    self.ROWS,
                    navy=report_html.NAVY,
                    lgray=report_html.LGRAY,
                    mgray=report_html.MGRAY,
                    right_cols=right_cols,
                    total_row=total_row,
                )
                self.assertEqual(weekly, shared)

    def test_table_uses_bgcolor_so_outlook_keeps_the_banding(self):
        html = report_html.data_table(self.HEADERS, self.ROWS)
        self.assertIn("<table", html)
        self.assertIn(report_html.NAVY, html)


def _close(*figs):
    import matplotlib.pyplot as plt

    for fig in figs:
        plt.close(fig)


if __name__ == "__main__":
    unittest.main()
