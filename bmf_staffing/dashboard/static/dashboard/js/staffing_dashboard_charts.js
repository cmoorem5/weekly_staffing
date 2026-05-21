/**
 * Staffing dashboard Chart.js wiring (labels + series from JSON script tags).
 */
(function () {
  function readJsonScript(id) {
    const el = document.getElementById(id);
    if (!el || !el.textContent.trim()) {
      return null;
    }
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  const labels = readJsonScript("staffing-chart-labels");
  const staffingRate = readJsonScript("staffing-chart-staffing-rate");
  const otDependency = readJsonScript("staffing-chart-ot-dependency");
  const managerLineShiftsTotal = readJsonScript("staffing-chart-mgr-total");
  const managerLineShiftsBreakdown = readJsonScript("staffing-chart-mgr-breakdown");
  const excTotal = readJsonScript("staffing-chart-exc-total");
  const excBreakdown = readJsonScript("staffing-chart-exc-breakdown");

  if (!labels || typeof Chart === "undefined") {
    return;
  }

  function movingAverage3(series) {
    const out = [];
    for (let i = 0; i < series.length; i++) {
      if (i < 2) {
        out.push(null);
        continue;
      }
      const a = series[i - 2],
        b = series[i - 1],
        c = series[i];
      if (
        a === null ||
        b === null ||
        c === null ||
        a === undefined ||
        b === undefined ||
        c === undefined
      ) {
        out.push(null);
        continue;
      }
      out.push((Number(a) + Number(b) + Number(c)) / 3.0);
    }
    return out;
  }

  function lineChart(el, series, label, color, ySuffix) {
    const ctx = document.getElementById(el);
    if (!ctx) return;
    return new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: label,
            data: series,
            borderColor: color,
            backgroundColor: "rgba(0,0,0,0)",
            tension: 0.2,
            pointRadius: 2,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { display: false } },
        scales: {
          y: { ticks: { callback: (v) => (ySuffix ? v + ySuffix : v) } },
        },
      },
    });
  }

  lineChart("chartStaffingRate", staffingRate, "Staffing rate", "#0b3d91", "%");
  lineChart("chartOt", otDependency, "OT dependency", "#b31b1b", "%");

  const mgrChartCanvas = document.getElementById("chartManagerLineShifts");
  const mgrChart = mgrChartCanvas
    ? new Chart(mgrChartCanvas, {
        type: "bar",
        data: { labels: labels, datasets: [] },
        options: {
          responsive: true,
          plugins: {
            legend: { position: "bottom" },
            tooltip: { mode: "index", intersect: false },
          },
          scales: {
            x: { stacked: true },
            y: { stacked: true, beginAtZero: true },
          },
        },
      })
    : null;

  const excChartCanvas = document.getElementById("chartExceptions");
  const excChart = excChartCanvas
    ? new Chart(excChartCanvas, {
        type: "bar",
        data: { labels: labels, datasets: [] },
        options: {
          responsive: true,
          plugins: {
            legend: { position: "bottom" },
            tooltip: { mode: "index", intersect: false },
          },
          scales: {
            x: { stacked: true },
            y: { stacked: true, beginAtZero: true },
          },
        },
      })
    : null;

  (function () {
    const excModeBreakdown = document.getElementById("excModeBreakdown");
    const excModeTotal = document.getElementById("excModeTotal");
    const modeLabel = document.getElementById("excChartModeLabel");
    const includeOther = document.getElementById("id_exc_include_other");
    const excTrendMode = document.getElementById("excTrendMode");
    if (!excChart || !excModeBreakdown || !excModeTotal || !modeLabel || !includeOther) return;

    includeOther.checked = false;

    const colors = {
      LT: "#0b3d91",
      LOA: "#5c2d91",
      SICK: "#b31b1b",
      AT: "#052c47",
      JURY: "#198754",
      BREV: "#6f42c1",
      Other: "#6c757d",
      Total: "#c12126",
      Trend: "#212529",
    };

    function addTrendOverlays(totalSeries, trendModeValue) {
      const mode = trendModeValue || "both";
      const trendEnabled = mode === "both" || mode === "trend";
      const trend = movingAverage3(totalSeries || []);

      if (trendEnabled) {
        excChart.data.datasets.push({
          type: "line",
          label: "Trend (3-period MA)",
          data: trend,
          borderColor: colors.Trend,
          backgroundColor: "rgba(0,0,0,0)",
          borderDash: [6, 4],
          tension: 0.2,
          pointRadius: 0,
          spanGaps: false,
          yAxisID: "y",
          order: 0,
        });
      }
    }

    function buildBreakdownDatasets() {
      const base = [
        { key: "LT", label: "LT", color: colors.LT },
        { key: "LOA", label: "LOA", color: colors.LOA },
        { key: "SICK", label: "SICK/SL", color: colors.SICK },
        { key: "AT", label: "AT", color: colors.AT },
        { key: "JURY", label: "JURY", color: colors.JURY },
        { key: "BREV", label: "BREV", color: colors.BREV },
      ];
      if (includeOther.checked) {
        base.push({ key: "Other", label: "Other", color: colors.Other });
      }
      return base.map((d) => ({
        label: d.label,
        data: excBreakdown && excBreakdown[d.key] ? excBreakdown[d.key] : [],
        backgroundColor: d.color,
        borderColor: d.color,
        borderWidth: 0,
        order: 2,
      }));
    }

    function setExcMode(mode) {
      const isTotal = mode === "total";
      const trendMode = excTrendMode ? excTrendMode.value : "both";
      if (isTotal) {
        excChart.options.scales.x.stacked = false;
        excChart.options.scales.y.stacked = false;
        excChart.data.datasets = [];
        if (trendMode === "both" || trendMode === "actual") {
          excChart.data.datasets.push({
            type: "bar",
            label: "Total exceptions",
            data: excTotal || [],
            backgroundColor: colors.Total,
            borderColor: colors.Total,
            order: 3,
          });
        }
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(excTotal || [], trendMode);
        }
        modeLabel.textContent = "Total";
      } else {
        excChart.options.scales.x.stacked = true;
        excChart.options.scales.y.stacked = true;
        const stacked = buildBreakdownDatasets();
        excChart.data.datasets = trendMode === "trend" ? [] : stacked;
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(excTotal || [], trendMode);
        }
        modeLabel.textContent = "Breakdown";
      }
      excChart.update();
    }

    excModeBreakdown.addEventListener("change", () => {
      if (excModeBreakdown.checked) setExcMode("breakdown");
    });
    excModeTotal.addEventListener("change", () => {
      if (excModeTotal.checked) setExcMode("total");
    });
    includeOther.addEventListener("change", () => {
      if (excModeBreakdown.checked) setExcMode("breakdown");
    });
    if (excTrendMode) {
      excTrendMode.addEventListener("change", () => {
        setExcMode(excModeTotal.checked ? "total" : "breakdown");
      });
    }

    setExcMode(excModeBreakdown.checked ? "breakdown" : "total");
  })();

  (function () {
    const mgrModeBreakdown = document.getElementById("mgrModeBreakdown");
    const mgrModeTotal = document.getElementById("mgrModeTotal");
    const modeLabel = document.getElementById("mgrChartModeLabel");
    const mgrTrendMode = document.getElementById("mgrTrendMode");
    if (!mgrChart || !mgrModeBreakdown || !mgrModeTotal || !modeLabel) return;

    const colors = {
      Total: "#052c47",
      Trend: "#212529",
      Bars: ["#0b3d91", "#5c2d91", "#b31b1b", "#198754", "#6c757d", "#0dcaf0", "#fd7e14", "#6610f2"],
    };

    function addTrendOverlays(totalSeries, trendModeValue) {
      const mode = trendModeValue || "both";
      const trendEnabled = mode === "both" || mode === "trend";
      const trend = movingAverage3(totalSeries || []);

      if (trendEnabled) {
        mgrChart.data.datasets.push({
          type: "line",
          label: "Trend (3-period MA)",
          data: trend,
          borderColor: colors.Trend,
          backgroundColor: "rgba(0,0,0,0)",
          borderDash: [6, 4],
          tension: 0.2,
          pointRadius: 0,
          spanGaps: false,
          yAxisID: "y",
          order: 0,
        });
      }
    }

    function buildBreakdownDatasets() {
      const keys = managerLineShiftsBreakdown ? Object.keys(managerLineShiftsBreakdown) : [];
      return keys.map((k, idx) => {
        const color = colors.Bars[idx % colors.Bars.length];
        return {
          type: "bar",
          label: k,
          data: managerLineShiftsBreakdown[k] || [],
          backgroundColor: color,
          borderColor: color,
          borderWidth: 0,
          order: 2,
        };
      });
    }

    function setMgrMode(mode) {
      const isTotal = mode === "total";
      const trendMode = mgrTrendMode ? mgrTrendMode.value : "both";
      if (isTotal) {
        mgrChart.options.scales.x.stacked = false;
        mgrChart.options.scales.y.stacked = false;
        mgrChart.data.datasets = [];
        if (trendMode === "both" || trendMode === "actual") {
          mgrChart.data.datasets.push({
            type: "bar",
            label: "Total manager line shifts",
            data: managerLineShiftsTotal || [],
            backgroundColor: colors.Total,
            borderColor: colors.Total,
            order: 3,
          });
        }
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(managerLineShiftsTotal || [], trendMode);
        }
        modeLabel.textContent = "Total";
      } else {
        mgrChart.options.scales.x.stacked = true;
        mgrChart.options.scales.y.stacked = true;
        mgrChart.data.datasets = trendMode === "trend" ? [] : buildBreakdownDatasets();
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(managerLineShiftsTotal || [], trendMode);
        }
        modeLabel.textContent = "Breakdown";
      }
      mgrChart.update();
    }

    mgrModeBreakdown.addEventListener("change", () => {
      if (mgrModeBreakdown.checked) setMgrMode("breakdown");
    });
    mgrModeTotal.addEventListener("change", () => {
      if (mgrModeTotal.checked) setMgrMode("total");
    });
    if (mgrTrendMode) {
      mgrTrendMode.addEventListener("change", () => {
        setMgrMode(mgrModeTotal.checked ? "total" : "breakdown");
      });
    }
    setMgrMode(mgrModeBreakdown.checked ? "breakdown" : "total");
  })();
})();
