/**
 * Manager shifts dashboard Chart.js wiring.
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

  if (typeof Chart === "undefined") {
    return;
  }

  const periodLabels = readJsonScript("mgr-chart-labels");
  const stackedSeries = readJsonScript("mgr-chart-stacked");
  const chartColors = readJsonScript("mgr-chart-colors") || [];
  const progressLabels = readJsonScript("mgr-chart-progress-labels");
  const progressShifts = readJsonScript("mgr-chart-progress-shifts");
  const progressTargets = readJsonScript("mgr-chart-progress-targets");
  const progressMet = readJsonScript("mgr-chart-progress-met");

  const byPeriodEl = document.getElementById("chartManagerByPeriod");
  if (byPeriodEl && periodLabels && stackedSeries) {
    const managers = Object.keys(stackedSeries);
    const datasets = managers.map((name, i) => ({
      label: name,
      data: stackedSeries[name] || [],
      backgroundColor: chartColors[i % chartColors.length] || "#2a4492",
      borderColor: chartColors[i % chartColors.length] || "#2a4492",
      borderWidth: 0,
    }));
    new Chart(byPeriodEl, {
      type: "bar",
      data: { labels: periodLabels, datasets },
      options: {
        responsive: true,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
        },
        scales: {
          x: { stacked: true },
          y: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }

  const progressEl = document.getElementById("chartManagerProgress");
  if (progressEl && progressLabels && progressShifts) {
    const shiftColors = (progressMet || []).map((met) =>
      met ? "rgba(42, 68, 146, 0.85)" : "rgba(193, 33, 38, 0.75)"
    );
    new Chart(progressEl, {
      type: "bar",
      data: {
        labels: progressLabels,
        datasets: [
          {
            label: "Shifts worked",
            data: progressShifts,
            backgroundColor: shiftColors.length ? shiftColors : "rgba(42, 68, 146, 0.85)",
            borderColor: shiftColors.length ? shiftColors : "rgba(42, 68, 146, 0.85)",
            borderWidth: 0,
            order: 2,
          },
          {
            label: "Prorated minimum",
            data: progressTargets || [],
            type: "bar",
            backgroundColor: "rgba(0,0,0,0)",
            borderColor: "#495057",
            borderWidth: 2,
            order: 1,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              afterBody: function (items) {
                if (!items.length) return "";
                const idx = items[0].dataIndex;
                const shifts = progressShifts[idx];
                const target = progressTargets ? progressTargets[idx] : null;
                if (target == null || target === 0) return "";
                const delta = shifts - target;
                const sign = delta >= 0 ? "+" : "";
                return `Δ vs min: ${sign}${delta.toFixed(1)}`;
              },
            },
          },
        },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0 } },
        },
      },
    });
  }
})();
