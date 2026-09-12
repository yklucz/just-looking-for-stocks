import { buildChartData, latestViewport, preservedViewport, visiblePriceRange, marketDrawingPlugin, validCandle, axisLabel } from './chart-data.js';

const state = {
  ticker: "AAPL",
  range: "1m",
  interval: "auto",
  priceField: "close",
  showFullHistory: false,
  historyData: [],
  theme: "light",
  overlay: "both",
  chartPredictions: null,
  chartInterval: "auto",
  chartType: 'line', futureBars: 10, visibleBars: 60, autoRefresh: true,
  ...globalThis.StockPreferences?.read(),
};
const saveSettings = () => globalThis.StockPreferences?.save(state);
Chart.register(marketDrawingPlugin);
if (globalThis.ChartZoom) {
  Chart.register(globalThis.ChartZoom);
}
const crosshairPlugin = {
  id: "crosshair",
  afterDatasetsDraw(chart) {
    const enabled = chart.options?.plugins?.crosshair?.enabled;
    if (!enabled) return;
  const active = chart.getActiveElements?.();
  if (!active?.length) return;
  const { ctx, chartArea } = chart;
  const { left, right, top, bottom } = chartArea;
  const { element } = active[0];
  const x = element.x;
  const y = element.y;
    const { datasetIndex, index } = active[0];
    const ds = chart.data.datasets?.[datasetIndex];
    const val = ds?.data?.[index];
    const price = typeof val === "object" && val !== null ? val.y ?? val : val;
  const label = chart.data.labels?.[index];
  const styles = getComputedStyle(document.documentElement);
  const textColor = styles.getPropertyValue("--text").trim() || "#2f3437";
  const panelColor = styles.getPropertyValue("--panel").trim() || "#ffffff";
  const borderColor = styles.getPropertyValue("--border").trim() || "#eaeaea";
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = borderColor || "#94a3b8";
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(left, y);
    ctx.lineTo(right, y);
    ctx.stroke();
    if (label) {
      const labelText = String(label);
      const padding = 6;
      const height = 20;
      ctx.font = "12px Helvetica Neue, sans-serif";
      const width = ctx.measureText(labelText).width + padding * 2;
      const lx = Math.min(Math.max(x - width / 2, left + 2), right - width - 2);
      const ly = top + 4;
      ctx.fillStyle = panelColor || "#f8fafc";
      ctx.strokeStyle = borderColor || "#e2e8f0";
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") {
        ctx.roundRect(lx, ly, width, height, 4);
      } else {
        ctx.rect(lx, ly, width, height);
      }
      ctx.fillStyle = panelColor || "#f8fafc";
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.fillText(labelText, lx + padding, ly + height - 6);
    }
    if (price !== undefined) {
      const txt = typeof price === "number" ? price.toLocaleString("en-US", { maximumFractionDigits: 2 }) : String(price);
      const padding = 6;
      const height = 20;
      ctx.font = "12px Helvetica Neue, sans-serif";
      const width = ctx.measureText(txt).width + padding * 2;
      const rx = right - width - 6;
      const ry = y - height / 2;
      ctx.fillStyle = panelColor || "#f8fafc";
      ctx.strokeStyle = borderColor || "#e2e8f0";
      ctx.lineWidth = 1;
      ctx.beginPath();
      if (typeof ctx.roundRect === "function") {
        ctx.roundRect(rx, ry, width, height, 4);
      } else {
        ctx.rect(rx, ry, width, height);
      }
      ctx.fillStyle = panelColor || "#f8fafc";
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = textColor;
      ctx.fillText(txt, rx + padding, ry + height - 6);
    }
    ctx.restore();
  },
};
Chart.register(crosshairPlugin);
let quickPickList = [];
let priceChart;
let searchTimer = null;
let searchSeq = 0;
let activeLoadToken = 0;
let chartDataKey = null;
let followingLatest = true;
let predictionRequestToken = null;

const tickerInput = document.getElementById("tickerInput");
const rangeTabs = document.getElementById("rangeTabs");
const intervalSelect = document.getElementById("intervalSelect");
const priceFieldSelect = document.getElementById("priceFieldSelect");
const toggleHistoryRowsBtn = document.getElementById("toggleHistoryRows");
const themeToggle = document.getElementById("themeToggle");
rangeTabs.addEventListener("click", (e) => {
  const btn = e.target.closest(".range-btn");
  if (!btn) return;
  for (const b of document.querySelectorAll(".range-btn")) {
    b.classList.remove("active");
    b.setAttribute("aria-pressed", "false");
  }
  btn.classList.add("active");
  btn.setAttribute("aria-pressed", "true");
  state.range = btn.dataset.range;
  saveSettings();
  loadAll();
});

intervalSelect.addEventListener("change", () => {
  state.interval = intervalSelect.value;
  loadAll();
});

priceFieldSelect.addEventListener("change", () => {
  state.priceField = priceFieldSelect.value;
  saveSettings();
  redrawOverlays();
});

toggleHistoryRowsBtn.addEventListener("click", () => {
  state.showFullHistory = !state.showFullHistory;
  saveSettings();
  renderHistoryTable(state.historyData);
});

function applyTheme(theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
  themeToggle.textContent = theme === "dark" ? "Light mode" : "Dark mode";
  themeToggle.setAttribute("aria-pressed", String(theme === "dark"));
}

themeToggle.addEventListener("click", () => {
  state.theme = state.theme === "dark" ? "light" : "dark";
  applyTheme(state.theme);
  saveSettings();
  redrawOverlays();
});
document.getElementById("searchForm").addEventListener("submit", (e) => {
  e.preventDefault();
  const val = tickerInput.value.trim();
  if (!val) return;
  state.ticker = val.toUpperCase();
  loadAll();
});

tickerInput.addEventListener("input", (e) => {
  const val = e.target.value.trim();
  clearTimeout(searchTimer);
  if (val.length < 2) {
    searchSeq += 1;
    renderOptions(quickPickList);
    return;
  }
  searchTimer = setTimeout(() => {
    runSearch(val);
  }, 200);
});

async function fetchJSON(url, options = {}) {
  const res = await fetch(url, { cache: "no-store", ...options });
  let data;
  try {
    data = await res.json();
  } catch {
    throw new Error(`Request failed (${res.status})`);
  }
  if (!res.ok || data.error) {
    const error = new Error(data.error || "Request failed");
    error.code = data.code;
    throw error;
  }
  return data;
}

function formatUSD(num) {
  if (num === null || num === undefined || Number.isNaN(num)) return "—";
  return "$" + Number(num).toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function setCursor(el, cursor) {
  if (el) el.style.cursor = cursor;
}

function priceFieldLabel(value) {
  switch ((value || "").toLowerCase()) {
    case "open":
      return "Open";
    case "high":
      return "High";
    case "low":
      return "Low";
    case "current":
      return "Current";
    case "close":
    default:
      return "Close";
  }
}

function trendClass(current, previous) {
  if (current === null || current === undefined || previous === null || previous === undefined) return "";
  if (Number.isNaN(current) || Number.isNaN(previous)) return "";
  if (current > previous) return "price-up";
  if (current < previous) return "price-down";
  return "";
}

function zoomOptions(canvas) {
  const styles = getComputedStyle(document.documentElement);
  const textColor = styles.getPropertyValue("--text").trim() || "#2f3437";
  const borderColor = styles.getPropertyValue("--border").trim() || "#eaeaea";
  const gridColor = styles.getPropertyValue("--grid")?.trim() || borderColor;
  return {
    textColor,
    borderColor,
    // Pointer panning below owns dragging; avoid a second Hammer pan handler.
    pan: { enabled: false, mode: "x", threshold: 0 },
    zoom: {
      wheel: { enabled: true, speed: 0.05, modifierKey: null },
      pinch: { enabled: true },
      drag: {
        enabled: true,
        modifierKey: "shift",
        mode: "x",
        borderColor: "#526950",
        backgroundColor: "rgba(82,105,80,0.08)",
        threshold: 6,
      },
      mode: "x",
      onZoom: ({ chart }) => updateVisibleScale(chart),
      onZoomComplete: ({ chart }) => rememberViewport(chart),
    },
    scales: {
      x: {
        type: 'category', offset: true,
        ticks: { color: textColor, maxRotation: 0, maxTicksLimit: 6, autoSkipPadding: 18,
          callback(value) { return axisLabel(this.getLabelForValue(value)); }, font: { size: 10 } },
        grid: { display: false },
        border: { display: false },
      },
      y: {
        position: "right",
        border: { display: false },
        ticks: { color: textColor, maxTicksLimit: 5, font: { size: 10 } },
        grid: { color: gridColor },
      },
    },
    legendColor: textColor,
  };
}

const panHandlers = new WeakMap();
function attachPanHandlers(chart, canvas) {
  if (!canvas || !chart || typeof chart.pan !== "function") return;
  const prev = panHandlers.get(canvas);
  if (prev) {
    canvas.removeEventListener("pointerdown", prev.down);
    canvas.removeEventListener("pointermove", prev.move);
    canvas.removeEventListener("pointerup", prev.up);
    canvas.removeEventListener("pointerleave", prev.up);
    canvas.removeEventListener("pointercancel", prev.up);
  }
  let isPanning = false;
  let lastX = 0;
  let pending = { x: 0, y: 0 };
  let raf = null;
  const flushPan = () => {
    raf = null;
    if (!isPanning) return;
    chart.pan({ x: pending.x }, [chart.scales.x], "none");
    updateVisibleScale(chart);
    pending = { x: 0, y: 0 };
  };
  const down = (e) => {
    // Skip if using shift for box-zoom or not primary button.
    if (e.shiftKey || e.button !== 0 || e.isPrimary === false) return;
    e.preventDefault();
    isPanning = true;
    lastX = e.clientX;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch (error_) {
      console.error(error_);
    }
    setCursor(canvas, "grabbing");
  };
  const move = (e) => {
    if (!isPanning) return;
    e.preventDefault();
    const dx = e.clientX - lastX;
    pending.x += dx;
    lastX = e.clientX;
    if (!raf) raf = requestAnimationFrame(flushPan);
  };
  const up = (e) => {
    if (!isPanning) return;
    if (raf) { cancelAnimationFrame(raf); flushPan(); }
    isPanning = false;
    rememberViewport(chart);
    pending = { x: 0, y: 0 };
    setCursor(canvas, "grab");
    try {
      canvas.releasePointerCapture(e.pointerId);
    } catch (error_) {
      console.error(error_);
    }
  };
  canvas.addEventListener("pointerdown", down);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointerleave", up);
  canvas.addEventListener("pointercancel", up);
  panHandlers.set(canvas, { down, move, up });
}

function setError(msg) {
  const el = document.getElementById("error");
  if (msg) {
    el.style.display = "block";
    el.textContent = msg;
  } else {
    el.style.display = "none";
    el.textContent = "";
  }
}

function chartLoaderEl(key) {
  return document.querySelector(`[data-chart-loader="${key}"]`);
}

function showChartLoader(key, title, message) {
  const el = chartLoaderEl(key);
  if (!el) return;
  const titleEl = el.querySelector(".chart-loader__title");
  const msgEl = el.querySelector(".chart-loader__message");
  const spinner = el.querySelector(".chart-loader__spinner");
  if (titleEl && title) titleEl.textContent = title;
  if (msgEl && message) msgEl.textContent = message;
  if (spinner) spinner.style.display = "block";
  el.classList.remove("error");
  el.style.display = "flex";
}

function showChartError(key, message) {
  const el = chartLoaderEl(key);
  if (!el) return;
  const titleEl = el.querySelector(".chart-loader__title");
  const msgEl = el.querySelector(".chart-loader__message");
  if (titleEl) titleEl.textContent = "Unable to load chart";
  if (msgEl) msgEl.textContent = message || "Something went wrong.";
  el.classList.add("error");
  el.style.display = "flex";
}

function hideChartLoader(key) {
  const el = chartLoaderEl(key);
  if (!el) return;
  el.style.display = "none";
  el.classList.remove("error");
}

async function loadInfo(loadToken, ticker) {
  const info = await fetchJSON(`/api/info?ticker=${encodeURIComponent(ticker)}&t=${Date.now()}`);
  if (loadToken !== activeLoadToken) return;
  document.getElementById("infoName").textContent = info.name || "—";
  document.getElementById("infoSymbol").textContent = info.symbol ? info.symbol : "—";
  document.getElementById("infoSector").textContent = info.sector || "—";
  document.getElementById("infoIndustry").textContent = info.industry || "—";
  document.getElementById("infoPrice").textContent = formatUSD(info.currentPrice);
  document.getElementById("infoMarketCap").textContent = info.marketCap ? info.marketCap.toLocaleString("en-US") : "—";
  const range = (info.fiftyTwoWeekLow || "—") + " / " + (info.fiftyTwoWeekHigh || "—");
  document.getElementById("info52w").textContent = range;
  document.getElementById("infoWebsite").textContent = info.website || "—";
}

function renderHistoryTable(data) {
  const tbody = document.querySelector("#historyTable tbody");
  const existing = new Map(Array.from(tbody.children).map(row => [row.dataset.timestamp, row]));
  const rows = state.showFullHistory ? data : data.slice(-10);
  const startIdx = state.showFullHistory ? 0 : Math.max(0, data.length - rows.length);
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    const p = rows[i];
    const prev = data[startIdx + i - 1] || null;
    const cls = trendClass(p.Close, prev?.Close);
    const tr = existing.get(p.Date) || document.createElement("tr");
    tr.dataset.timestamp = p.Date;
    existing.delete(p.Date);
    let column = 0;
    for (const key of ["Date", "Open", "High", "Low", "Close", "Volume"]) {
      const cell = tr.children[column] || document.createElement("td");
      let value = "—";

      if (key === "Date") {
        value = p.Date;
      } else if (Number.isFinite(p[key])) {
        const decimals = key === "Volume" ? 0 : 2;
        value = p[key].toLocaleString("en-US", {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        });
      }

      if (cell.textContent !== value) cell.textContent = value;
      if (key === 'Close') {
        cell.classList.toggle('price-up', cls === 'price-up');
        cell.classList.toggle('price-down', cls === 'price-down');
      }
      if (!tr.children[column]) tr.appendChild(cell);
      column++;
    }
    const position = rows.length - 1 - i;
    if (tbody.children[position] !== tr) tbody.insertBefore(tr, tbody.children[position] || null);
  }
  for (const row of existing.values()) row.remove();
  const meta = document.getElementById("historyRowsMeta");
  const btn = document.getElementById("toggleHistoryRows");
  meta.textContent = state.showFullHistory ? `Showing all ${data.length}` : `Showing latest ${rows.length} of ${data.length}`;
  btn.textContent = state.showFullHistory ? "Show summary" : "Show all";
  btn.setAttribute("aria-expanded", String(state.showFullHistory));
}

async function loadHistory(loadToken, ticker, range, interval, priceField, background = false) {
  if (!background) showChartLoader("price", `Preparing ${priceFieldLabel(priceField)} chart…`, "Fetching price history");
  const query = new URLSearchParams({
    ticker,
    range,
    t: Date.now().toString(),
  });
  if (interval && interval !== "auto") {
    query.set("interval", interval);
  }
  try {
    const data = await fetchJSON(`/api/history?${query.toString()}`);
    if (loadToken !== activeLoadToken) return;
    const prices = data.prices || [];
    if (!prices.length) throw new Error('No history received; previous chart retained.');
    state.historyData = prices;
    renderHistoryTable(prices);
    let intervalLabel = "auto";
    if (data.interval) {
      intervalLabel = data.interval;
    } else if (interval && interval !== "auto") {
      intervalLabel = interval;
    }
    document.getElementById("priceMeta").textContent = `${data.symbol} • ${range.toUpperCase()} • ${intervalLabel} • ${prices.length} rows`;

    state.chartInterval = intervalLabel;
    renderPriceChart(prices, range, state.priceField, intervalLabel);
    hideChartLoader("price");
  } catch (err) {
    if (loadToken === activeLoadToken && !background) {
      showChartError("price", err?.message || "Unable to load price chart.");
    }
    throw err;
  }
}

function chartColors() {
  const styles = getComputedStyle(document.documentElement);
  return { price: styles.getPropertyValue('--chart-line').trim(),
    up: styles.getPropertyValue('--positive').trim(), down: styles.getPropertyValue('--danger').trim(),
    forecast: state.theme === 'dark' ? '#e3b777' : '#a26929',
    probability: state.theme === 'dark' ? '#bca8df' : '#7d609e',
    future: state.theme === 'dark' ? 'rgba(227,183,119,.035)' : 'rgba(162,105,41,.035)' };
}

function updateVisibleScale(chart) {
  if (!chart?.$marketData) return;
  const bounds = { min: chart.scales.x.min, max: chart.scales.x.max };
  const scale = chart.options.scales.y;
  delete scale.min; delete scale.max;
  Object.assign(scale, visiblePriceRange(chart.$marketData, bounds));
  chart.update('none');
  updateChartViewStatus(chart);
}

function rememberViewport(chart) {
  if (!chart?.$marketData) return;
  state.visibleBars = Math.max(2, Math.round(chart.scales.x.max - chart.scales.x.min + 1));
  followingLatest = chart.scales.x.max >= chart.$marketData.labels.length - 2;
  updateChartViewStatus(chart);
  saveSettings();
}

function latestView(bars = state.visibleBars) {
  if (!priceChart?.$marketData) return;
  state.visibleBars = bars;
  followingLatest = true;
  Object.assign(priceChart.options.scales.x, latestViewport(priceChart.$marketData, bars));
  priceChart.update('none');
  updateVisibleScale(priceChart);
  saveSettings();
}

function updateChartViewStatus(chart) {
  if (!chart?.$marketData) return;
  const count = Math.max(0, Math.min(chart.$marketData.historyCount, Math.floor(chart.scales.x.max) + 1)
    - Math.max(0, Math.ceil(chart.scales.x.min)));
  document.getElementById('chartViewStatus').textContent = `${count} visible candles · ${followingLatest ? 'Following latest' : 'Historical view'} `;
}

function chartTooltip(item) {
  const row = priceChart?.$marketData.points[item.dataIndex];
  if (item.dataset.id === 'observed' && state.chartType === 'candle') {
    if (!row || !validCandle(row)) return 'No complete OHLC candle';
    return `Open ${formatUSD(row.Open)} · High ${formatUSD(row.High)} · Low ${formatUSD(row.Low)} · Close ${formatUSD(row.Close)}`;
  }
  const value = item.parsed.y;
  if (item.dataset.id === 'probability') return `Model probability: ${value.toFixed(1)}%`;
  const label = item.dataset.id === 'forecast' ? 'Horizon connector endpoint' : item.dataset.label;
  return `${label}: ${formatUSD(value)}`;
}

function renderPriceChart(prices, range, priceField, intervalLabel) {
  const colors = chartColors();
  const data = buildChartData(prices, { ...state, interval: intervalLabel, priceField }, state.chartPredictions, colors);
  const key = `${state.ticker}:${range}:${intervalLabel}`;
  const sameHistory = priceChart && key === chartDataKey;
  if (!sameHistory) followingLatest = true;
  const view = sameHistory ? preservedViewport(priceChart.$marketData, data,
    priceChart.scales.x, followingLatest, state.visibleBars) : latestViewport(data, state.visibleBars);
  document.getElementById('priceChartLabel').textContent = state.chartType === 'candle'
    ? 'Candlesticks · Open / High / Low / Close' : `Price chart (${priceFieldLabel(priceField)})`;
  priceFieldSelect.disabled = state.chartType === 'candle';
  const canvas = document.getElementById('priceChart');
  const zoomOpts = zoomOptions(canvas);
  const scales = { ...zoomOpts.scales, probability: {
    position: 'left', min: 0, max: 100, display: data.datasets.some(d => d.id === 'probability'),
    title: { display: true, text: 'Event probability (%)', color: zoomOpts.textColor },
    ticks: { color: zoomOpts.textColor, callback: value => `${value}%` }, grid: { drawOnChartArea: false },
  } };
  Object.assign(scales.x, view);
  Object.assign(scales.y, visiblePriceRange(data, view));
  const plugins = {
    legend: { display: true, labels: { color: zoomOpts.legendColor, boxWidth: 12, font: { size: 10 } } },
    zoom: { zoom: zoomOpts.zoom, pan: zoomOpts.pan,
      limits: { x: { min: 0, max: data.labels.length - 1, minRange: 1 } } },
    crosshair: { enabled: true }, tooltip: { callbacks: { label: chartTooltip } },
  };
  if (!priceChart) {
    priceChart = new Chart(canvas.getContext('2d'), { type: 'line',
      data: { labels: data.labels, datasets: data.datasets },
      options: { animation: false, responsive: true, maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false }, scales, plugins } });
    attachPanHandlers(priceChart, canvas);
  } else {
    // Keep the canvas, dataset objects and interaction state. Replace values
    // at their timestamps; a newly received candle is appended, not replayed.
    const oldDatasets = new Map(priceChart.data.datasets.map(d => [d.id, d]));
    priceChart.data.labels = data.labels;
    priceChart.data.datasets = data.datasets.map(next => {
      const previous = oldDatasets.get(next.id);
      if (!previous) return next;
      Object.assign(previous, next);
      return previous;
    });
    Object.assign(priceChart.options.scales.x, scales.x);
    delete priceChart.options.scales.y.min; delete priceChart.options.scales.y.max;
    Object.assign(priceChart.options.scales.y, scales.y);
    Object.assign(priceChart.options.scales.probability, scales.probability);
    priceChart.options.plugins = plugins;
  }
  priceChart.$marketData = data;
  priceChart.$colors = colors;
  chartDataKey = key;
  priceChart.update('none');
  updateChartViewStatus(priceChart);
  setCursor(canvas, 'grab');
  updateOverlayStatus();
}

async function loadChartPredictions(loadToken, ticker) {
  const status = document.getElementById('overlayStatus');
  try {
    let result = await fetchJSON(`/api/prediction-chart?${new URLSearchParams({ticker})}`);
    if (loadToken !== activeLoadToken) return;
    state.chartPredictions = result;
    redrawOverlays();
    if (result.forecast_status === 'missing' && ['both', 'forecast'].includes(state.overlay)) {
      while (loadToken === activeLoadToken) {
        status.textContent = 'Training the separate return-regression model for the price estimate…';
        const job = await fetchJSON('/api/train', { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker, task: 'regression' }) });
        if (job.status === 'ready') {
          result = await fetchJSON(`/api/prediction-chart?${new URLSearchParams({ticker})}`);
          break;
        }
        if (job.status !== 'training') throw new Error(job.error || 'Return model training failed');
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }
    if (loadToken !== activeLoadToken) return;
    state.chartPredictions = result;
    redrawOverlays();
    updateOverlayStatus();
  } catch (error) {
    if (loadToken === activeLoadToken) status.textContent = `Chart estimates unavailable: ${error.message}`;
  }
}

function redrawOverlays() {
  if (state.historyData.length) renderPriceChart(state.historyData, state.range, state.priceField, state.chartInterval);
}

function updateOverlayStatus() {
  document.getElementById('overlayStatus').textContent = [
    priceChart?.$marketData.notes,
    state.chartPredictions?.forecast_status === 'unavailable' ? state.chartPredictions.forecast_error : '',
    'Shaded slots are future space, not scheduled trading dates. Purple uses the % axis.',
  ].filter(Boolean).join(' ');
}

const predictionFields = ["predictionValue", "predictionTime", "predictionHorizon", "predictionTarget", "predictionSignal", "predictionModel", "predictionTrained", "predictionVersion"];
function clearPrediction(message = "Loading saved model…") {
  for (const id of predictionFields) document.getElementById(id).textContent = "—";
  document.getElementById("predictionStatus").textContent = message;
}

async function loadPrediction(loadToken, ticker, background = false) {
  try {
    const url = `/api/predict?${new URLSearchParams({ ticker, model: "xgboost" })}`;
    let data;
    try {
      data = await fetchJSON(url);
    } catch (error) {
      if (error.code !== "MODEL_NOT_AVAILABLE") throw error;
      while (loadToken === activeLoadToken) {
        clearPrediction("Training XGBoost for this stock… You can continue exploring the chart.");
        const job = await fetchJSON('/api/train', {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticker }),
        });
        if (job.status === "ready") { data = await fetchJSON(url); break; }
        if (job.status !== "training") throw new Error(job.error || "Training failed");
        await new Promise(resolve => setTimeout(resolve, 2000));
      }
    }
    if (loadToken !== activeLoadToken) return;
    const p = data.prediction;
    if (!p || !Number.isFinite(p.probability_up) || p.probability_up < 0 || p.probability_up > 1) throw new Error("Invalid model probability response");
    document.getElementById("predictionValue").textContent = `${(100*p.probability_up).toFixed(1)}%`;
    document.getElementById("predictionTime").textContent = data.timestamp.slice(0,10);
    document.getElementById("predictionHorizon").textContent = `${p.horizon} daily candles`;
    document.getElementById("predictionTarget").textContent = `Future log return > +${(100*p.event_threshold).toFixed(2)}%`;
    document.getElementById("predictionSignal").textContent = `${data.signal.action} (cutoff ${data.signal.decision_threshold})`;
    document.getElementById("predictionModel").textContent = data.model.type;
    document.getElementById("predictionTrained").textContent = data.model.trained_until.slice(0,10);
    document.getElementById("predictionVersion").textContent = data.model.version.slice(0,12);
    document.getElementById("predictionStatus").textContent = [
      "Uncalibrated model probability. Research only; economic value is not established.",
      data.model.stale ? "Model is stale; offline retraining is required to refresh it." : "",
      data.market.stale ? "Market data is stale." : "",
    ].filter(Boolean).join(" ");
    await loadChartPredictions(loadToken, ticker);
  } catch (err) {
    if (loadToken === activeLoadToken) {
      if (background && document.getElementById('predictionValue').textContent !== '—') {
        document.getElementById('predictionStatus').textContent = `Update unavailable; showing the previous estimate. ${err.message}`;
      } else clearPrediction(err.message || "Prediction unavailable");
    }
    throw err;
  }
}

function renderOptions(list) {
  const dl = document.getElementById("symbolList");
  dl.innerHTML = "";
  for (const { symbol, name } of list) {
    const opt = document.createElement("option");
    opt.value = symbol;
    opt.label = name;
    dl.appendChild(opt);
  }
}

async function loadQuickPicks() {
  try {
    const list = await fetchJSON(`/api/symbols?t=${Date.now()}`);
    quickPickList = list;
    renderOptions(list);
    const qp = document.getElementById("quickPicks");
    qp.innerHTML = "";
    for (const { symbol, name } of list) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.title = `${symbol} — ${name}`;
      chip.className = "chip";
      chip.textContent = symbol;
      chip.onclick = () => {
        state.ticker = symbol;
        tickerInput.value = symbol;
        loadAll();
      };
      qp.appendChild(chip);
    }
  } catch (err) {
    console.error(err);
    setError("Unable to load symbols right now.");
  }
}

async function runSearch(query) {
  const seq = ++searchSeq;
  try {
    const results = await fetchJSON(`/api/search?q=${encodeURIComponent(query)}&t=${Date.now()}`);
    if (seq !== searchSeq) return;
    renderOptions(results);
  } catch (err) {
    console.error(err);
    setError("Search unavailable right now.");
  }
}

let refreshTimer = null;
let activeRefreshToken = null;
function scheduleRefresh() {
  if (!globalThis.window) return;
  clearTimeout(refreshTimer);
  if (!state.autoRefresh || document.hidden) return;
  refreshTimer = setTimeout(() => { if (activeRefreshToken === null) loadAll(true); }, 30000);
}

async function loadAll(background = false) {
  if (background && activeRefreshToken !== null) return;
  if (!background) {
    state.ticker = (tickerInput.value.trim() || state.ticker || "").toUpperCase();
    tickerInput.value = state.ticker;
    state.interval = intervalSelect.value || "auto";
    state.priceField = priceFieldSelect.value || "close";
    activeLoadToken += 1;
    saveSettings();
  }
  clearTimeout(refreshTimer);
  const loadToken = activeLoadToken;
  activeRefreshToken = loadToken;
  if (!background) showChartLoader("price", "Preparing price chart…", "Fetching latest candles");
  if (!background) {
    document.querySelector("#historyTable tbody").innerHTML = "";
    document.getElementById("priceMeta").textContent = "Loading...";
    clearPrediction();
    state.historyData = [];
    state.chartPredictions = null;
    document.getElementById('overlayStatus').textContent = '';
  }
  setError("");
  document.getElementById('refreshNow').disabled = true;
  document.getElementById('refreshStatus').textContent = 'Checking for updates…';
  // Training may take minutes; price/quote polling continues independently.
  let predictionTask = null;
  if (predictionRequestToken !== loadToken) {
    predictionRequestToken = loadToken;
    predictionTask = loadPrediction(loadToken, state.ticker, background).catch(error => {
      if (loadToken === activeLoadToken) setError(`Prediction: ${error.message}`);
    }).finally(() => { if (predictionRequestToken === loadToken) predictionRequestToken = null; });
  }
  const tasks = [
    { name: "Info", run: () => loadInfo(loadToken, state.ticker) },
    { name: "History", run: () => loadHistory(loadToken, state.ticker, state.range, state.interval, state.priceField, background) },
  ];
  const results = await Promise.allSettled(tasks.map((t) => t.run()));
  if (loadToken !== activeLoadToken) return;
  const errors = tasks
    .map((task, idx) => ({ task: task.name, result: results[idx] }))
    .filter((entry) => entry.result.status === "rejected")
    .map((entry) => `${entry.task}: ${entry.result.reason?.message || "Request failed"}`);
  if (errors.length) setError(errors.join(" • "));
  document.getElementById('refreshStatus').textContent = `${errors.length ? 'Last attempt' : 'Updated'} ${new Date().toLocaleTimeString()} · Yahoo may be delayed`;
  activeRefreshToken = null;
  document.getElementById('refreshNow').disabled = false;
  scheduleRefresh();
  await predictionTask;
}

function revealSections() {
  if (!globalThis.IntersectionObserver || globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.remove("reveal-pending");
        observer.unobserve(entry.target);
      }
    }
  }, { threshold: 0.05 });
  document.querySelectorAll(".reveal").forEach((section, index) => {
    section.style.transitionDelay = `${Math.min(index % 3, 2) * 80}ms`;
    section.classList.add("reveal-pending");
    observer.observe(section);
  });
}

async function bootstrap() {
  revealSections();
  tickerInput.value = state.ticker;
  intervalSelect.value = state.interval;
  priceFieldSelect.value = state.priceField;
  document.getElementById('overlaySelect').value = state.overlay;
  document.getElementById('chartTypeSelect').value = state.chartType;
  document.getElementById('futureBarsSelect').value = String(state.futureBars);
  document.getElementById('autoRefresh').checked = state.autoRefresh;
  for (const button of document.querySelectorAll('.range-btn')) {
    button.classList.toggle('active', button.dataset.range === state.range);
    button.setAttribute('aria-pressed', String(button.dataset.range === state.range));
  }
  applyTheme(state.theme);
  await loadQuickPicks();
  await loadAll();
}

document.getElementById('overlaySelect').addEventListener('change', async (event) => {
  state.overlay = event.target.value;
  saveSettings();
  redrawOverlays();
  if (state.chartPredictions?.forecast_status === 'missing' && ['both', 'forecast'].includes(state.overlay) && activeRefreshToken === null) {
    await loadAll(true);
  }
});
document.getElementById('chartTypeSelect').addEventListener('change', (event) => {
  state.chartType = event.target.value;
  saveSettings(); redrawOverlays();
});
document.getElementById('futureBarsSelect').addEventListener('change', (event) => {
  state.futureBars = Number(event.target.value);
  followingLatest = true;
  saveSettings(); redrawOverlays();
});
document.getElementById('zoomIn').addEventListener('click', () => {
  priceChart?.zoom({ x: 1.5 }, 'none'); updateVisibleScale(priceChart); rememberViewport(priceChart);
});
document.getElementById('zoomOut').addEventListener('click', () => {
  priceChart?.zoom({ x: .67 }, 'none'); updateVisibleScale(priceChart); rememberViewport(priceChart);
});
document.getElementById('maxPriceZoom').addEventListener('click', () => latestView((priceChart?.$marketData.futureCount || state.futureBars) + 2));
document.getElementById('fitPriceZoom').addEventListener('click', () => latestView(priceChart?.$marketData.labels.length || 60));
document.getElementById('resetPriceZoom').addEventListener('click', () => latestView());
document.getElementById('autoRefresh').addEventListener('change', (event) => {
  state.autoRefresh = event.target.checked;
  saveSettings(); scheduleRefresh();
});
document.getElementById('refreshNow').addEventListener('click', () => { if (activeRefreshToken === null) loadAll(true); });
if (document.addEventListener) document.addEventListener('visibilitychange', scheduleRefresh);

await bootstrap();
