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
  const shiftException = readJsonScript("staffing-chart-shift-exception");
  const systemRw = readJsonScript("staffing-chart-system-rw");
  const systemGr = readJsonScript("staffing-chart-system-gr");
  const weeksPerBucket = readJsonScript("staffing-chart-weeks-per-bucket") || [];
  const targets = readJsonScript("staffing-chart-targets") || {};
  const baseOrder = readJsonScript("staffing-chart-base-order") || [];

  // Categorical slots, assigned in fixed order (validated for color-vision
  // deficiency; the old navy/purple pairs were indistinguishable under protanopia).
  const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7", "#008300", "#e34948"];
  const NEUTRAL = "#6c757d";
  const TARGET = "#212529";

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

  // Count charts: per-week average (default) or raw period total. Periods hold
  // 1-5 weeks (partial edges, 4- vs 5-week months), so raw totals read a short
  // period as a drop.
  function scaleSeries(series, mode) {
    if (mode !== "per_week" || !series) return series || [];
    return series.map((v, i) => {
      const n = weeksPerBucket[i];
      if (v === null || v === undefined || !n) return v;
      return Math.round((Number(v) / n) * 10) / 10;
    });
  }

  function scaleLabelText(mode) {
    return mode === "per_week" ? "(per week)" : "(period total)";
  }

  function weeksNote(items) {
    const n = items.length ? weeksPerBucket[items[0].dataIndex] : null;
    return n ? n + (n === 1 ? " week" : " weeks") : "";
  }

  // Dashed, in the series' own color so two targets on one chart stay attributable.
  function targetDataset(key, label, color) {
    const value = targets[key];
    if (value === undefined || value === null) return null;
    return {
      label: label + " target (" + value + "%)",
      data: labels.map(() => value),
      borderColor: color,
      borderWidth: 1.5,
      borderDash: [6, 4],
      pointRadius: 0,
      pointHitRadius: 0,
      backgroundColor: "rgba(0,0,0,0)",
    };
  }

  // series: [{label, data, color, targetKey}]
  function lineChart(el, series) {
    const ctx = document.getElementById(el);
    if (!ctx) return;
    const datasets = [];
    series.forEach((s) => {
      datasets.push({
        label: s.label,
        data: s.data || [],
        borderColor: s.color,
        backgroundColor: s.color,
        borderWidth: 2,
        tension: 0.2,
        pointRadius: 3,
        pointHoverRadius: 5,
      });
    });
    series.forEach((s) => {
      const t = s.targetKey ? targetDataset(s.targetKey, s.label, s.color) : null;
      if (t) datasets.push(t);
    });
    return new Chart(ctx, {
      type: "line",
      data: { labels: labels, datasets: datasets },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: datasets.length > 1, position: "bottom" },
          tooltip: {
            callbacks: {
              label: (c) => c.dataset.label + ": " + c.formattedValue + "%",
              footer: weeksNote,
            },
          },
        },
        scales: {
          y: { ticks: { callback: (v) => v + "%" } },
        },
      },
    });
  }

  lineChart("chartStaffingRate", [
    { label: "Staffing rate", data: staffingRate, color: SERIES[0], targetKey: "staffing_rate" },
  ]);
  lineChart("chartOt", [
    { label: "OT dependency", data: otDependency, color: SERIES[1], targetKey: "ot_dependency" },
  ]);
  lineChart("chartCoverage", [
    { label: "System RW", data: systemRw, color: SERIES[0], targetKey: "system_rw" },
    { label: "System GR", data: systemGr, color: SERIES[1], targetKey: "system_gr" },
  ]);
  lineChart("chartShiftException", [
    { label: "Shift exception", data: shiftException, color: SERIES[2], targetKey: "shift_exception" },
  ]);

  const mgrChartCanvas = document.getElementById("chartManagerLineShifts");
  const mgrChart = mgrChartCanvas
    ? new Chart(mgrChartCanvas, {
        type: "bar",
        data: { labels: labels, datasets: [] },
        options: {
          responsive: true,
          plugins: {
            legend: { position: "bottom" },
            tooltip: { mode: "index", intersect: false, callbacks: { footer: weeksNote } },
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
            tooltip: { mode: "index", intersect: false, callbacks: { footer: weeksNote } },
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
    const excScale = document.getElementById("excScale");
    const excScaleLabel = document.getElementById("excScaleLabel");
    const scaleMode = () => (excScale ? excScale.value : "total");
    if (!excChart || !excModeBreakdown || !excModeTotal || !modeLabel || !includeOther) return;

    includeOther.checked = false;

    const colors = {
      LT: SERIES[0],
      LOA: SERIES[1],
      SICK: SERIES[2],
      AT: SERIES[3],
      JURY: SERIES[4],
      BREV: SERIES[5],
      Other: NEUTRAL,
      Total: SERIES[0],
      Trend: TARGET,
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
        data: scaleSeries(excBreakdown && excBreakdown[d.key] ? excBreakdown[d.key] : [], scaleMode()),
        backgroundColor: d.color,
        borderColor: "#ffffff",
        borderWidth: { top: 2 },
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
            data: scaleSeries(excTotal, scaleMode()),
            backgroundColor: colors.Total,
            borderColor: colors.Total,
            order: 3,
          });
        }
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(scaleSeries(excTotal, scaleMode()), trendMode);
        }
        modeLabel.textContent = "Total";
      } else {
        excChart.options.scales.x.stacked = true;
        excChart.options.scales.y.stacked = true;
        const stacked = buildBreakdownDatasets();
        excChart.data.datasets = trendMode === "trend" ? [] : stacked;
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(scaleSeries(excTotal, scaleMode()), trendMode);
        }
        modeLabel.textContent = "Breakdown";
      }
      if (excScaleLabel) excScaleLabel.textContent = scaleLabelText(scaleMode());
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
    [excTrendMode, excScale].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", () => {
        setExcMode(excModeTotal.checked ? "total" : "breakdown");
      });
    });

    setExcMode(excModeBreakdown.checked ? "breakdown" : "total");
  })();

  (function () {
    const mgrModeBreakdown = document.getElementById("mgrModeBreakdown");
    const mgrModeTotal = document.getElementById("mgrModeTotal");
    const modeLabel = document.getElementById("mgrChartModeLabel");
    const mgrTrendMode = document.getElementById("mgrTrendMode");
    const mgrScale = document.getElementById("mgrScale");
    const mgrScaleLabel = document.getElementById("mgrScaleLabel");
    const scaleMode = () => (mgrScale ? mgrScale.value : "total");
    if (!mgrChart || !mgrModeBreakdown || !mgrModeTotal || !modeLabel) return;

    const colors = {
      Total: SERIES[0],
      Trend: TARGET,
    };

    // Color follows the base, not its position in this window's legend: a base
    // with no shifts in the selected range must not repaint the others.
    function baseColor(name) {
      const i = baseOrder.indexOf(name);
      return i >= 0 && i < SERIES.length ? SERIES[i] : NEUTRAL;
    }

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
      return keys.map((k) => {
        const color = baseColor(k);
        return {
          type: "bar",
          label: k,
          data: scaleSeries(managerLineShiftsBreakdown[k], scaleMode()),
          backgroundColor: color,
          borderColor: "#ffffff",
          borderWidth: { top: 2 },
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
            data: scaleSeries(managerLineShiftsTotal, scaleMode()),
            backgroundColor: colors.Total,
            borderColor: colors.Total,
            order: 3,
          });
        }
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(scaleSeries(managerLineShiftsTotal, scaleMode()), trendMode);
        }
        modeLabel.textContent = "Total";
      } else {
        mgrChart.options.scales.x.stacked = true;
        mgrChart.options.scales.y.stacked = true;
        mgrChart.data.datasets = trendMode === "trend" ? [] : buildBreakdownDatasets();
        if (trendMode === "both" || trendMode === "trend") {
          addTrendOverlays(scaleSeries(managerLineShiftsTotal, scaleMode()), trendMode);
        }
        modeLabel.textContent = "Breakdown";
      }
      if (mgrScaleLabel) mgrScaleLabel.textContent = scaleLabelText(scaleMode());
      mgrChart.update();
    }

    mgrModeBreakdown.addEventListener("change", () => {
      if (mgrModeBreakdown.checked) setMgrMode("breakdown");
    });
    mgrModeTotal.addEventListener("change", () => {
      if (mgrModeTotal.checked) setMgrMode("total");
    });
    [mgrTrendMode, mgrScale].forEach((el) => {
      if (!el) return;
      el.addEventListener("change", () => {
        setMgrMode(mgrModeTotal.checked ? "total" : "breakdown");
      });
    });
    setMgrMode(mgrModeBreakdown.checked ? "breakdown" : "total");
  })();
})();
