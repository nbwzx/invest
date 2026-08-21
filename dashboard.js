// ================================================================
//  SHARED UTILITIES
// ================================================================

const getNumeric = (val) =>
  val !== null && val !== undefined && !isNaN(Number(val)) ? Number(val) : null;

const fmtPct = (val) => {
  const num = getNumeric(val);
  return num !== null ? (num * 100).toFixed(2) + "%" : "—";
};

const fmtNum = (val, d = 2) => {
  const num = getNumeric(val);
  return num !== null ? num.toFixed(d) : "—";
};

const fmtCurrency = (val) => {
  const num = getNumeric(val);
  return num !== null ? num.toFixed(2) : "—";
};

const formatDate = (d) => d.toISOString().slice(0, 10);

// ================================================================
//  1. QQQ CHART MODULE
// ================================================================

(function () {
  "use strict";

  const runBtn = document.getElementById("runBtn");
  const qqqTbody = document.getElementById("qqq-table-body");
  const paramsList = document.getElementById("params-list");
  const ctx = document.getElementById("returnsChart").getContext("2d");
  const dataCountSpan = document.getElementById("dataCount");
  const statusMsg = document.getElementById("statusMsg");
  const qqqGenTime = document.getElementById("qqqGeneratedTime");

  let rawData = null;
  let chartInstance = null;
  let defaultParams = {};
  let globalCoeffs = null;
  let globalStartDate = null;

  function setStatus(msg, type = "info") {
    statusMsg.textContent = msg;
    statusMsg.className = "status";
    if (type === "success") statusMsg.classList.add("success");
    if (type === "error") statusMsg.classList.add("error");
    console.log("[Status]", msg);
  }

  function computeMaxDrawdown(values) {
    let peak = values[0];
    let maxDD = 0;
    for (const v of values) {
      if (v > peak) peak = v;
      const dd = (peak - v) / peak;
      if (dd > maxDD) maxDD = dd;
    }
    return maxDD;
  }

  function getIntervals(condition) {
    const intervals = [];
    let inInterval = false;
    let start = 0;
    for (let i = 0; i < condition.length; i++) {
      if (condition[i] && !inInterval) {
        inInterval = true;
        start = i;
      } else if (!condition[i] && inInterval) {
        inInterval = false;
        intervals.push([start, i - 1]);
      }
    }
    if (inInterval) intervals.push([start, condition.length - 1]);
    return intervals;
  }

  function linearFit(x, y) {
    const n = x.length;
    let sumX = 0,
      sumY = 0,
      sumXX = 0,
      sumXY = 0;
    for (let i = 0; i < n; i++) {
      sumX += x[i];
      sumY += y[i];
      sumXX += x[i] * x[i];
      sumXY += x[i] * y[i];
    }
    const det = n * sumXX - sumX * sumX;
    let a, b;
    if (Math.abs(det) < 1e-12) {
      a = 0;
      b = 0;
    } else {
      a = (sumXX * sumY - sumX * sumXY) / det;
      b = (n * sumXY - sumX * sumY) / det;
    }
    return { coeffs: [a, b] };
  }

  async function loadData() {
    try {
      setStatus("Loading data...", "info");
      const resp = await fetch("qqq_pe_data.json");
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      const json = await resp.json();
      rawData = json.data.map((d) => ({
        Date: new Date(d.Date),
        Close: parseFloat(d.Close),
        PE: parseFloat(d.PE),
      }));
      defaultParams = json.default_parameters;
      dataCountSpan.textContent = rawData.length;

      if (json.generated_at) {
        const dt = new Date(json.generated_at);
        qqqGenTime.textContent = "📅 Data generated: " + dt.toLocaleString();
      } else {
        qqqGenTime.textContent = "";
      }

      setStatus(`Data loaded: ${rawData.length} rows`, "success");

      const valid = rawData.filter((r) => !isNaN(r.PE));
      if (valid.length === 0) throw new Error("No valid PE data");
      globalStartDate = valid[0].Date;
      const x = valid.map(
        (r) => (r.Date - globalStartDate) / (24 * 60 * 60 * 1000),
      );
      const y = valid.map((r) => r.PE);
      const { coeffs } = linearFit(x, y);
      globalCoeffs = coeffs;
      console.log("Global linear fit:", globalCoeffs);

      document.getElementById("initCapital").value =
        defaultParams.initial_capital || 10000;
      document.getElementById("threshold").value = 10;
      const today = new Date();
      const threeYearsAgo = new Date(today);
      threeYearsAgo.setFullYear(today.getFullYear() - 3);
      document.getElementById("startDate").value = threeYearsAgo
        .toISOString()
        .slice(0, 10);
      document.getElementById("endDate").value = today
        .toISOString()
        .slice(0, 10);

      setTimeout(runSimulation, 100);
    } catch (err) {
      console.error("Error loading data:", err);
      setStatus("❌ Failed to load data: " + err.message, "error");
      alert(
        "Could not load qqq_pe_data.json. Make sure the file is in the same folder.",
      );
    }
  }

  function simulateBuyAndHold(data, params) {
    const { initCapital, threshold } = params;
    const valid = data.filter(
      (r) => r.PE !== null && r.PE !== undefined && !isNaN(r.PE),
    );
    if (valid.length === 0)
      throw new Error("No valid PE data in selected date range");

    const startDate = valid[0].Date;
    const endDate = valid[valid.length - 1].Date;
    const firstClose = valid[0].Close;

    const fittedPE = valid.map((r) => {
      const days = (r.Date - globalStartDate) / (24 * 60 * 60 * 1000);
      return globalCoeffs[0] + globalCoeffs[1] * days;
    });

    const upperBand = fittedPE.map((v) => v * (1 + threshold / 100));
    const lowerBand = fittedPE.map((v) => v * (1 - threshold / 100));
    const peVals = valid.map((r) => r.PE);
    const buyCondition = peVals.map((pe, i) => pe < lowerBand[i]);
    const sellCondition = peVals.map((pe, i) => pe > upperBand[i]);
    const buyIntervals = getIntervals(buyCondition);
    const sellIntervals = getIntervals(sellCondition);

    const returns = valid.map((row) => row.Close / firstClose - 1);
    const finalValue =
      initCapital * (valid[valid.length - 1].Close / firstClose);
    const maxDD = computeMaxDrawdown(valid.map((r) => r.Close));
    const years = (endDate - startDate) / (365.25 * 24 * 60 * 60 * 1000);
    const annReturn =
      years > 0 ? Math.pow(finalValue / initCapital, 1 / years) - 1 : NaN;

    const result = {
      name: "Nasdaq Buy & Hold",
      finalValue: finalValue,
      annualReturn: annReturn,
      maxDrawdown: maxDD,
      dates: valid.map((r) => r.Date),
      returns: returns,
    };

    return {
      result: result,
      peData: valid.map((r) => ({ Date: r.Date, PE: r.PE })),
      fittedPE: fittedPE,
      coeffs: globalCoeffs,
      buyIntervals: buyIntervals,
      sellIntervals: sellIntervals,
      params: { initCapital, threshold, startDate, endDate },
    };
  }

  const shadingPlugin = {
    id: "shadingPlugin",
    intervals: { buy: [], sell: [] },
    afterDraw(chart) {
      const { ctx, scales, chartArea } = chart;
      const { top, bottom, left, right } = chartArea;
      const xScale = scales.x;
      const yScale = scales.y1;
      if (!yScale) return;

      const drawBoxes = (intervals, color) => {
        intervals.forEach(([startIdx, endIdx]) => {
          const x1 = xScale.getPixelForValue(startIdx);
          const x2 = xScale.getPixelForValue(endIdx);
          const drawLeft = Math.max(left, Math.min(x1, right));
          const drawRight = Math.max(left, Math.min(x2, right));
          if (drawRight <= drawLeft) return;
          const yMinPx = yScale.getPixelForValue(yScale.max);
          const yMaxPx = yScale.getPixelForValue(yScale.min);
          const drawTop = Math.max(top, Math.min(yMinPx, bottom));
          const drawBottom = Math.max(top, Math.min(yMaxPx, bottom));
          ctx.save();
          ctx.fillStyle = color;
          ctx.fillRect(
            drawLeft,
            drawTop,
            drawRight - drawLeft,
            drawBottom - drawTop,
          );
          ctx.restore();
        });
      };
      drawBoxes(this.intervals.buy, "rgba(0, 200, 0, 0.2)");
      drawBoxes(this.intervals.sell, "rgba(255, 0, 0, 0.2)");
    },
  };

  function renderResults(simData) {
    const {
      result,
      peData,
      fittedPE,
      coeffs,
      buyIntervals,
      sellIntervals,
      params,
    } = simData;

    qqqTbody.innerHTML = "";
    const ann = result.annualReturn;
    const annCls =
      ann !== null && !isNaN(ann)
        ? ann > 0
          ? "positive"
          : ann < 0
            ? "negative"
            : ""
        : "";
    const ddCls = result.maxDrawdown > 0 ? "negative" : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `
        <td>${result.name}</td>
        <td>${fmtCurrency(result.finalValue)}</td>
        <td class="${annCls}">${fmtPct(ann)}</td>
        <td class="${ddCls}">${fmtPct(result.maxDrawdown)}</td>
      `;
    qqqTbody.appendChild(tr);

    paramsList.innerHTML = `
        <li class="list-group-item">Initial Capital: $${params.initCapital}</li>
        <li class="list-group-item">Threshold: ${params.threshold}%</li>
        <li class="list-group-item">Date Range: ${formatDate(params.startDate)} – ${formatDate(
          params.endDate,
        )}</li>
        <li class="list-group-item">Global linear coefficients: a=${coeffs[0].toFixed(
          4,
        )}, b=${coeffs[1].toFixed(4)}</li>
        <li class="list-group-item">Buy zones: ${buyIntervals.length}, Sell zones: ${sellIntervals.length}</li>
      `;

    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }

    const labels = result.dates.map((d) => formatDate(d));
    const peValues = peData.map((d) => d.PE);

    shadingPlugin.intervals.buy = buyIntervals;
    shadingPlugin.intervals.sell = sellIntervals;

    if (!Chart.registry.plugins.get("shadingPlugin")) {
      Chart.register(shadingPlugin);
    }

    chartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: result.name,
            data: result.returns,
            borderColor: "#2c3e50",
            backgroundColor: "transparent",
            borderWidth: 3,
            tension: 0.1,
            pointRadius: 0,
            fill: false,
            yAxisID: "y",
          },
          {
            label: "PE Ratio",
            data: peValues,
            borderColor: "rgba(0, 0, 255, 0.4)",
            backgroundColor: "transparent",
            borderWidth: 1,
            pointRadius: 0,
            fill: false,
            yAxisID: "y1",
            tension: 0.1,
          },
          {
            label: "Fitted PE (Global Linear)",
            data: fittedPE,
            borderColor: "rgba(255, 165, 0, 0.5)",
            backgroundColor: "transparent",
            borderWidth: 1,
            borderDash: [4, 4],
            pointRadius: 0,
            fill: false,
            yAxisID: "y1",
            tension: 0.1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { position: "top" },
          tooltip: {
            callbacks: {
              label: function (context) {
                let label = context.dataset.label || "";
                let val = context.parsed.y;
                if (context.dataset.yAxisID === "y1") {
                  return label + ": " + val.toFixed(2);
                } else {
                  return label + ": " + (val * 100).toFixed(2) + "%";
                }
              },
            },
          },
        },
        scales: {
          x: { type: "category", labels: labels, ticks: { maxTicksLimit: 20 } },
          y: {
            type: "linear",
            display: true,
            position: "left",
            ticks: { callback: (v) => (v * 100).toFixed(0) + "%" },
            title: { display: true, text: "Cumulative Return" },
          },
          y1: {
            type: "linear",
            display: true,
            position: "right",
            grid: { drawOnChartArea: false },
            ticks: { callback: (v) => v.toFixed(1) },
            title: { display: true, text: "PE Ratio" },
          },
        },
      },
      plugins: [shadingPlugin],
    });
  }

  function runSimulation() {
    try {
      setStatus("Running simulation...", "info");
      if (!rawData || !globalCoeffs) {
        setStatus("❌ Data not ready", "error");
        alert("Data not loaded yet. Please wait or reload.");
        return;
      }

      const initCapital =
        parseFloat(document.getElementById("initCapital").value) || 10000;
      const startDate = document.getElementById("startDate").value;
      const endDate = document.getElementById("endDate").value;
      const threshold =
        parseFloat(document.getElementById("threshold").value) || 10;

      if (!startDate || !endDate) {
        setStatus("❌ Please select start and end dates", "error");
        alert("Please select both start and end dates.");
        return;
      }

      const start = new Date(startDate);
      const end = new Date(endDate);
      if (isNaN(start.getTime()) || isNaN(end.getTime())) {
        setStatus("❌ Invalid date format", "error");
        alert("Invalid date format.");
        return;
      }

      let filtered = rawData.filter((d) => d.Date >= start && d.Date <= end);
      if (filtered.length === 0) {
        setStatus("❌ No data in selected date range", "error");
        alert("No data in selected date range. Please adjust dates.");
        return;
      }

      const params = { initCapital, threshold };
      const simResult = simulateBuyAndHold(filtered, params);
      renderResults(simResult);
      setStatus(`✅ Done – ${filtered.length} days`, "success");
    } catch (err) {
      console.error("Simulation error:", err);
      setStatus("❌ Error: " + err.message, "error");
      alert("Error in simulation: " + err.message);
    }
  }

  document
    .getElementById("qqq-tab")
    .addEventListener("shown.bs.tab", function () {
      if (chartInstance) {
        chartInstance.resize();
      } else if (!rawData) {
        loadData();
      } else {
        runSimulation();
      }
    });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadData);
  } else {
    loadData();
  }
  runBtn.addEventListener("click", runSimulation);
})();

// ================================================================
//  2. FUND DASHBOARD MODULE – FULLY DYNAMIC (headers too)
// ================================================================

(function () {
  "use strict";

  // ---- CONFIGURATION ----
  // Periods that have a return column (used in the header and body)
  const returnPeriods = ["1M", "3M", "6M", "1Y", "3Y"];
  // All periods for metrics (including 2Y, which has no return)
  const periods = ["1M", "3M", "6M", "1Y", "2Y", "3Y"];
  const metrics = [
    "Correlation",
    "WinRate",
    "SameDirectionRate",
    "Beta",
    "Sharpe",
    "Calmar",
  ];
  const returnSuffix = "Return";
  const scoreKey = "TotalScore";

  // Calculate total columns dynamically for the empty state
  const fixedCols = 6; // #, Code, Name, Money, Limit, Fee
  const totalCols =
    fixedCols + returnPeriods.length + 1 + periods.length * metrics.length;

  // ---- DOM refs ----
  const thead = document.querySelector("#fund-table thead");
  const tbody = document.getElementById("table-body");
  const tfoot = document.getElementById("table-footer");
  const resetBtn = document.getElementById("resetSort");
  const resetMoneyBtn = document.getElementById("resetMoneyBtn");
  const clearMoneyBtn = document.getElementById("clearMoneyBtn");
  const fundGenTime = document.getElementById("fundGeneratedTime");
  const changeBody = document.getElementById("change-body");
  const changeGenTime = document.getElementById("changeGenTime");
  const fundSearch = document.getElementById("fundSearch");

  // ---- state ----
  let rawData = [];
  let currentData = [];
  let originalMoneyMap = {};
  let sortState = { key: null, direction: "none" };
  let showOnlyAvailable = true;
  let searchTerm = "";

  // colour‑able columns: period_Return and TotalScore
  const colorColumns = returnPeriods.map((p) => p + "_" + returnSuffix);
  colorColumns.push(scoreKey);

  const colorClass = (key, val) => {
    if (!colorColumns.includes(key)) return "";
    const num = getNumeric(val);
    if (num === null) return "";
    return num > 0 ? "positive" : num < 0 ? "negative" : "";
  };

  // ---- HEADER GENERATION (fully dynamic) ----
  function renderHeader() {
    let html = "<tr>";

    // 1. Fixed columns
    const fixedHeaders = [
      { key: "index", label: "#", title: "Row number" },
      { key: "Code", label: "Code", title: "Fund code" },
      { key: "Name", label: "Name", title: "Fund name" },
      { key: "Money", label: "Money", title: "Your allocation amount" },
      { key: "Limit", label: "Limit", title: "Daily purchase limit" },
      { key: "Fee", label: "Fee", title: "Purchase fee" },
    ];
    fixedHeaders.forEach((h) => {
      html += `<th data-sort-key="${h.key}" title="${h.title}">${h.label}<span class="sort-arrow"></span></th>`;
    });

    // 2. Return columns
    returnPeriods.forEach((p) => {
      html += `<th data-sort-key="${p}_${returnSuffix}" title="${p} cumulative return">${p} Ret<span class="sort-arrow"></span></th>`;
    });

    // 3. Score
    html += `<th data-sort-key="${scoreKey}" title="Weighted average of annual returns across periods">Score<span class="sort-arrow"></span></th>`;

    // 4. Metrics columns (periods × metrics)
    periods.forEach((p) => {
      metrics.forEach((m) => {
        let label;
        let title;
        if (m === "Correlation") {
          label = p + " r";
          title = `Correlation with QQQ over ${p}`;
        } else if (m === "WinRate") {
          label = p + " Win%";
          title = `Win rate vs QQQ over ${p}`;
        } else if (m === "SameDirectionRate") {
          label = p + " Same%";
          title = `Same direction frequency vs QQQ over ${p}`;
        } else if (m === "Beta") {
          label = p + " Beta";
          title = `Beta vs QQQ over ${p}`;
        } else if (m === "Sharpe") {
          label = p + " Sharpe";
          title = `Sharpe ratio over ${p}`;
        } else if (m === "Calmar") {
          label = p + " Calmar";
          title = `Calmar ratio over ${p}`;
        } else {
          label = p + " " + m;
          title = m + " over " + p;
        }
        html += `<th data-sort-key="${p}_${m}" title="${title}">${label}<span class="sort-arrow"></span></th>`;
      });
    });

    html += "</tr>";
    thead.innerHTML = html;
  }

  // ---- limit validation ----
  function checkLimit(input, limit) {
    const val = parseFloat(input.value);
    if (
      !isNaN(val) &&
      limit !== null &&
      limit !== undefined &&
      !isNaN(limit) &&
      val > limit
    ) {
      input.classList.add("exceeds-limit");
    } else {
      input.classList.remove("exceeds-limit");
    }
  }

  // ---- rendering (body) ----
  function renderTable(data) {
    if (!data || data.length === 0) {
      tbody.innerHTML = `<tr><td colspan="${totalCols}" class="text-center">No funds match your criteria</td></tr>`;
      tfoot.innerHTML = "";
      return;
    }

    let html = "";
    data.forEach((item, idx) => {
      const moneyVal =
        item.Money !== null && item.Money !== undefined ? item.Money : "";
      const limit = item.Limit;
      const limitDisplay = limit !== null && limit !== undefined ? limit : "—";
      const feeDisplay =
        item.Fee !== null && item.Fee !== undefined ? item.Fee : "—";
      const rowClass = idx % 2 === 0 ? "" : "table-light";

      html += `<tr class="${rowClass}">
        <td>${idx + 1}</td>
        <td>${item.Code || ""}</td>
        <td class="fund-name" title="${item.Name || ""}">${item.Name || ""}</td>
        <td>
          <input type="number" step="1" min="0" class="money-input" value="${moneyVal}"
                 data-index="${idx}" data-limit="${limit !== null && limit !== undefined ? limit : ""}">
        </td>
        <td>${limitDisplay}</td>
        <td>${feeDisplay}</td>`;

      // Return columns
      returnPeriods.forEach((p) => {
        const key = p + "_" + returnSuffix;
        const val = item[key];
        html += `<td class="${colorClass(key, val)}">${fmtPct(val)}</td>`;
      });

      // Score
      html += `<td class="${colorClass(scoreKey, item[scoreKey])}">${fmtPct(item[scoreKey])}</td>`;

      // Metrics columns
      periods.forEach((p) => {
        metrics.forEach((m) => {
          const key = p + "_" + m;
          const val = item[key];
          if (m === "Correlation" || m === "Beta") {
            html += `<td>${fmtNum(val, 4)}</td>`;
          } else if (m === "WinRate" || m === "SameDirectionRate") {
            html += `<td>${fmtPct(val)}</td>`;
          } else {
            html += `<td>${fmtNum(val, 3)}</td>`;
          }
        });
      });

      html += "</tr>";
    });
    tbody.innerHTML = html;

    // bind events to money inputs
    document.querySelectorAll(".money-input").forEach((input) => {
      const limit = parseFloat(input.dataset.limit);
      checkLimit(input, limit);
      input.addEventListener("change", onMoneyChange);
      input.addEventListener("input", onMoneyInput);
    });

    updateFooter(data);
    updateSortArrows();
  }

  // ---- money input handlers ----
  function onMoneyChange(e) {
    const input = e.target;
    const idx = parseInt(input.dataset.index);
    const newVal = parseFloat(input.value);
    if (!isNaN(newVal) && newVal >= 0) {
      currentData[idx].Money = newVal;
    } else {
      const prev = currentData[idx].Money;
      input.value = prev !== null && prev !== undefined ? prev : "";
    }
    const limit = parseFloat(input.dataset.limit);
    checkLimit(input, limit);
    updateFooter(currentData);
  }

  function onMoneyInput(e) {
    const input = e.target;
    const limit = parseFloat(input.dataset.limit);
    checkLimit(input, limit);
  }

  // ---- weighted‑average footer ----
  function updateFooter(data) {
    if (!data || data.length === 0) {
      tfoot.innerHTML = "";
      return;
    }

    let totalMoney = 0;
    const retSums = {};
    returnPeriods.forEach((p) => (retSums[p] = 0));
    let scoreSum = 0;

    data.forEach((item) => {
      const money = getNumeric(item.Money);
      if (money && money > 0) {
        totalMoney += money;
        returnPeriods.forEach((p) => {
          const r = getNumeric(item[p + "_" + returnSuffix]);
          if (r !== null) retSums[p] += money * r;
        });
        const s = getNumeric(item[scoreKey]);
        if (s !== null) scoreSum += money * s;
      }
    });

    const wavg = (sum, weight) => (weight > 0 ? sum / weight : null);
    const cls = (val) =>
      val !== null && val > 0
        ? "positive"
        : val !== null && val < 0
          ? "negative"
          : "";

    let footerCells = `
      <td colspan="2" class="text-start footer-label"><strong>Weighted Avg</strong></td>
      <td></td>
      <td><strong>${fmtNum(totalMoney, 0)}</strong></td>
      <td></td>
      <td></td>`;

    returnPeriods.forEach((p) => {
      const avg = wavg(retSums[p], totalMoney);
      footerCells += `<td class="${cls(avg)}"><strong>${fmtPct(avg)}</strong></td>`;
    });

    const avgScore = wavg(scoreSum, totalMoney);
    footerCells += `<td class="${cls(avgScore)}"><strong>${fmtPct(avgScore)}</strong></td>`;

    // empty placeholders for metric columns
    for (let i = 0; i < periods.length * metrics.length; i++) {
      footerCells += "<td></td>";
    }

    tfoot.innerHTML = `<tr>${footerCells}</tr>`;
  }

  // ---- filtering & sorting ----
  function refreshDisplay() {
    let data = rawData.slice();

    if (showOnlyAvailable) {
      data = data.filter((item) => {
        const limit = parseFloat(item.Limit);
        return !isNaN(limit) && limit > 0;
      });
    }

    if (searchTerm.trim() !== "") {
      const term = searchTerm.trim().toLowerCase();
      data = data.filter((item) => {
        const name = (item.Name || "").toLowerCase();
        const code = (item.Code || "").toLowerCase();
        return name.includes(term) || code.includes(term);
      });
    }

    if (sortState.key && sortState.direction !== "none") {
      const numericKeys = ["Money", "Limit", "Fee"];
      returnPeriods.forEach((p) => numericKeys.push(p + "_" + returnSuffix));
      numericKeys.push(scoreKey);
      periods.forEach((p) =>
        metrics.forEach((m) => numericKeys.push(p + "_" + m)),
      );

      const collator = new Intl.Collator(undefined, {
        numeric: true,
        sensitivity: "base",
      });

      data.sort((a, b) => {
        let va = a[sortState.key];
        let vb = b[sortState.key];
        if (numericKeys.includes(sortState.key)) {
          va = parseFloat(va);
          vb = parseFloat(vb);
          if (isNaN(va) && isNaN(vb)) return 0;
          if (isNaN(va)) return 1;
          if (isNaN(vb)) return -1;
          return sortState.direction === "asc" ? va - vb : vb - va;
        } else {
          va = (va || "").toString();
          vb = (vb || "").toString();
          const cmp = collator.compare(va, vb);
          return sortState.direction === "asc" ? cmp : -cmp;
        }
      });
    }

    currentData = data;
    renderTable(currentData);
  }

  // ---- sorting controls (event delegation) ----
  function setupSorting() {
    // Remove any previous listener to avoid duplicates
    if (thead._listener) {
      thead.removeEventListener("click", thead._listener);
    }
    const listener = function (e) {
      const th = e.target.closest("th");
      if (!th) return;
      const key = th.getAttribute("data-sort-key");
      if (!key) return;
      let nextDir;
      if (sortState.key === key) {
        if (sortState.direction === "asc") nextDir = "desc";
        else if (sortState.direction === "desc") nextDir = "none";
        else nextDir = "asc";
      } else {
        nextDir = "asc";
      }
      sortData(key, nextDir);
    };
    thead.addEventListener("click", listener);
    thead._listener = listener;

    // Buttons
    resetBtn.addEventListener("click", resetOrder);
    resetMoneyBtn.addEventListener("click", resetMoney);
    clearMoneyBtn.addEventListener("click", clearMoney);
    fundSearch.addEventListener("input", function () {
      searchTerm = this.value;
      refreshDisplay();
    });
  }

  function sortData(key, direction) {
    if (
      document.activeElement &&
      document.activeElement.classList.contains("money-input")
    ) {
      document.activeElement.blur();
    }
    if (direction === "none") {
      sortState = { key: null, direction: "none" };
    } else {
      sortState = { key, direction };
    }
    refreshDisplay();
    updateAriaSort();
  }

  function updateAriaSort() {
    document.querySelectorAll("#fund-table thead th").forEach((th) => {
      const key = th.getAttribute("data-sort-key");
      if (!key) return;
      if (key === sortState.key && sortState.direction !== "none") {
        th.setAttribute(
          "aria-sort",
          sortState.direction === "asc" ? "ascending" : "descending",
        );
      } else {
        th.setAttribute("aria-sort", "none");
      }
    });
  }

  function resetOrder() {
    sortState = { key: null, direction: "none" };
    refreshDisplay();
  }

  // ---- money actions ----
  function resetMoney() {
    rawData.forEach((item) => {
      const original = originalMoneyMap[item.Code];
      if (original !== undefined) {
        item.Money = original;
      }
    });
    refreshDisplay();
  }

  function clearMoney() {
    rawData.forEach((item) => {
      item.Money = 0;
    });
    refreshDisplay();
  }

  function updateSortArrows() {
    document.querySelectorAll("#fund-table thead th").forEach((th) => {
      const key = th.getAttribute("data-sort-key");
      const arrow = th.querySelector(".sort-arrow");
      if (!arrow) return;
      if (key === sortState.key && sortState.direction !== "none") {
        arrow.textContent = sortState.direction === "asc" ? "▲" : "▼";
      } else {
        arrow.textContent = "";
      }
    });
  }

  // ---- load data ----
  function initFund() {
    // Build the header first
    renderHeader();

    tbody.innerHTML = `<tr><td colspan="${totalCols}" class="text-center py-3">
      <span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Loading funds…
    </td></tr>`;

    fetch("funds.json")
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.json();
      })
      .then((data) => {
        if (data.generated_at) {
          const dt = new Date(data.generated_at);
          fundGenTime.textContent = "📅 Data generated: " + dt.toLocaleString();
        }

        rawData = data.funds.map((item) => {
          const copy = { ...item };
          if (
            copy.PurchaseStatus !== "限大额" &&
            copy.PurchaseStatus !== "开放申购"
          ) {
            copy.Limit = 0;
          }
          return copy;
        });

        // backup original money values
        originalMoneyMap = {};
        rawData.forEach((item) => {
          originalMoneyMap[item.Code] = item.Money;
        });

        refreshDisplay();
        setupSorting();
        document
          .getElementById("showAvailableOnly")
          .addEventListener("change", function () {
            showOnlyAvailable = this.checked;
            refreshDisplay();
          });
      })
      .catch((err) => {
        console.error("Failed to load funds.json:", err);
        tbody.innerHTML = `<tr><td colspan="${totalCols}" class="text-center text-danger">❌ Failed to load data: ${err.message}</td></tr>`;
      });

    loadLimitChanges();
  }

  function loadLimitChanges() {
    fetch("limit_changes.json")
      .then((res) => {
        if (!res.ok) {
          if (res.status === 404) {
            throw new Error("File not found (no changes yet)");
          }
          throw new Error("HTTP " + res.status);
        }
        return res.json();
      })
      .then((data) => {
        if (data.generated_at) {
          const dt = new Date(data.generated_at);
          changeGenTime.textContent = "📅 Updated: " + dt.toLocaleString();
        } else {
          changeGenTime.textContent = "";
        }

        const changes = data.changes || [];
        if (changes.length === 0) {
          changeBody.innerHTML =
            '<tr><td colspan="5" class="text-center">No limit changes recorded</td></tr>';
          return;
        }

        changes.sort((a, b) =>
          (b.change_date || "").localeCompare(a.change_date || ""),
        );

        let html = "";
        changes.forEach((item) => {
          html += `<tr>
            <td>${item.change_date || "—"}</td>
            <td>${item.code || ""}</td>
            <td>${item.name || ""}</td>
            <td>${item.old_limit !== null && item.old_limit !== undefined ? item.old_limit : "—"}</td>
            <td>${item.new_limit !== null && item.new_limit !== undefined ? item.new_limit : "—"}</td>
          </tr>`;
        });
        changeBody.innerHTML = html;
      })
      .catch((err) => {
        console.warn("Failed to load limit_changes.json:", err);
        changeBody.innerHTML =
          '<tr><td colspan="5" class="text-center text-muted">No limit change history available</td></tr>';
        changeGenTime.textContent = "";
      });
  }

  // ---- initialisation ----
  document
    .getElementById("fund-tab")
    .addEventListener("shown.bs.tab", function () {
      if (rawData.length === 0) initFund();
    });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initFund);
  } else {
    initFund();
  }
})();
