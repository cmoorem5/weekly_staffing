/**
 * Shift mix report Chart.js wiring.
 */
(function () {
  if (typeof Chart === "undefined") {
    return;
  }
  const dataEl = document.getElementById("shift-mix-chart-data");
  if (!dataEl) {
    return;
  }
  let data;
  try {
    data = JSON.parse(dataEl.textContent);
  } catch (e) {
    return;
  }
  if (!data) {
    return;
  }

  const DAY = "#e0a100";
  const NIGHT = "#2a4492";
  const RW = "#1f8a70";
  const GR = "#6c7a89";
  const REQ = "#c0392b";

  function donut(id, labels, values, colors) {
    const el = document.getElementById(id);
    if (!el) {
      return;
    }
    const total = values.reduce((a, b) => a + b, 0);
    new Chart(el, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }],
      },
      options: {
        maintainAspectRatio: false,
        cutout: "60%",
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const pct = total ? ((ctx.parsed / total) * 100).toFixed(1) : "0.0";
                return ctx.label + ": " + ctx.parsed + " (" + pct + "%)";
              },
            },
          },
        },
      },
    });
  }

  donut("chartDayNight", ["Day", "Night"], data.dayNight, [DAY, NIGHT]);
  donut("chartRwGr", ["RW", "GR"], data.rwGr, [RW, GR]);

  const basesEl = document.getElementById("chartBases");
  if (basesEl) {
    new Chart(basesEl, {
      type: "bar",
      data: {
        labels: data.bases.map((b) => b.label),
        datasets: [
          { label: "Day", data: data.bases.map((b) => b.day), backgroundColor: DAY },
          { label: "Night", data: data.bases.map((b) => b.night), backgroundColor: NIGHT },
        ],
      },
      options: {
        indexAxis: "y",
        maintainAspectRatio: false,
        scales: {
          x: { stacked: true, beginAtZero: true, ticks: { precision: 0 } },
          y: { stacked: true },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }

  const blocksEl = document.getElementById("chartBlocks");
  if (blocksEl) {
    const faded = (color, block) => (block.complete ? color : color + "66");
    new Chart(blocksEl, {
      data: {
        labels: data.blocks.map((b) => b.label),
        datasets: [
          {
            type: "bar",
            label: "Nights worked",
            data: data.blocks.map((b) => b.nights),
            backgroundColor: data.blocks.map((b) => faded(NIGHT, b)),
            order: 2,
          },
          {
            type: "line",
            label: "Nights required",
            data: data.blocks.map((b) => b.requiredNights),
            borderColor: REQ,
            backgroundColor: REQ,
            borderDash: [6, 4],
            pointStyle: "rectRot",
            stepped: true,
            order: 1,
          },
          {
            type: "bar",
            label: "Weekend shifts",
            data: data.blocks.map((b) => b.weekend),
            backgroundColor: data.blocks.map((b) => faded(RW, b)),
            order: 2,
          },
          {
            type: "line",
            label: "Weekend required (" + data.weekendTarget + ")",
            data: data.blocks.map(() => data.weekendTarget),
            borderColor: "#7a6400",
            backgroundColor: "#7a6400",
            borderDash: [2, 3],
            pointRadius: 0,
            order: 1,
          },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
        plugins: { legend: { position: "bottom" } },
      },
    });
  }
})();
