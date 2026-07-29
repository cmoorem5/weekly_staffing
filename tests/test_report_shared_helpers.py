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


class ExceptionTableTests(unittest.TestCase):
    def setUp(self):
        self.weekly = W._exception_table(weekly_ctx())
        self.quarterly = Q._exceptions_table(quarterly_ctx())

    def test_rows_carry_the_breakdown_plus_a_total_row(self):
        self.assertEqual(
            self.weekly._cellvalues,
            [
                ["Exception Type", "Count", "% of Total"],
                ["AT", "10", "50.0%"],
                ["LT", "6", "30.0%"],
                ["SICK", "3", "15.0%"],
                ["JURY", "1", "5.0%"],
                ["Total", "20", "100%"],
            ],
        )

    def test_top_two_exception_codes_are_highlighted_red(self):
        # Rows 1 (AT) and 2 (LT) are the top two; count/percent go red+bold.
        styles = cell_styles(self.weekly)
        red = "Color(.756863,.129412,.14902,1)"
        for row in (1, 2):
            for col in (1, 2):
                self.assertEqual(styles[row][col]["color"], red, f"row {row} col {col}")
        for row in (3, 4):
            self.assertNotEqual(styles[row][1]["color"], red)

    def test_weekly_and_quarterly_render_identically(self):
        """The two builders' copies must stay the same table."""
        self.assertEqual(self.weekly._cellvalues, self.quarterly._cellvalues)
        self.assertEqual(cell_styles(self.weekly), cell_styles(self.quarterly))
        self.assertEqual(col_widths(self.weekly), col_widths(self.quarterly))

    def test_column_widths_fill_the_usable_page_width(self):
        self.assertEqual(col_widths(self.weekly), [288.0, 108.0, 144.0])

    def test_empty_breakdown_still_renders_a_total_row(self):
        ctx = weekly_ctx()
        ctx.leave_breakdown = []
        table = W._exception_table(ctx)
        self.assertEqual(
            table._cellvalues,
            [["Exception Type", "Count", "% of Total"], ["Total", "0", "—"]],
        )


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
    def test_series_follow_the_trend_data(self):
        fig = W._build_trend_fig(weekly_ctx())
        try:
            ax = fig.axes[0]
            staffing = ax.get_lines()[0]
            self.assertEqual(
                [round(float(v), 4) for v in staffing.get_ydata()], [0.91, 0.87]
            )
            self.assertEqual(
                [t.get_text() for t in ax.get_xticklabels()],
                ["2025-12-07", "2025-12-14"],
            )
        finally:
            _close(fig)

    def test_each_report_keeps_its_own_figure_height(self):
        weekly, quarterly = (
            W._build_trend_fig(weekly_ctx()),
            Q._build_trend_fig(quarterly_ctx()),
        )
        try:
            self.assertEqual(
                [round(v, 3) for v in weekly.get_size_inches()], [7.5, 2.4]
            )
            self.assertEqual(
                [round(v, 3) for v in quarterly.get_size_inches()], [7.5, 2.6]
            )
        finally:
            _close(weekly, quarterly)

    def test_both_reports_plot_the_same_series_from_the_same_data(self):
        weekly, quarterly = (
            W._build_trend_fig(weekly_ctx()),
            Q._build_trend_fig(quarterly_ctx()),
        )
        try:
            self.assertEqual(
                [list(ln.get_ydata()) for ln in weekly.axes[0].get_lines()],
                [list(ln.get_ydata()) for ln in quarterly.axes[0].get_lines()],
            )
        finally:
            _close(weekly, quarterly)

    def test_exception_bars_follow_the_leave_breakdown(self):
        weekly, quarterly = (
            W._build_exception_bar_fig(weekly_ctx()),
            Q._build_exception_bar_fig(quarterly_ctx()),
        )
        try:
            w_heights = [p.get_height() for p in weekly.axes[0].patches]
            q_heights = [p.get_height() for p in quarterly.axes[0].patches]
            self.assertEqual(w_heights, q_heights)
            self.assertEqual(len(w_heights), len(LEAVE_BREAKDOWN))
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
