// Display geometry only. Blank future slots are not candles or model outputs.
export function dateLabel(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/.exec(String(value));
  if (!match) return value;
  const date = `${match[1]}/${match[2]}/${match[3]}`;
  return match[4] ? `${date} ${match[4]}` : date;
}

export function axisLabel(label) {
  if (/^\d{4}\/\d{2}\/\d{2}/.test(label)) return label.includes(' ') ? label.slice(-5) : label.slice(5);
  if (/^\d{4}-/.test(label)) return `+${label.match(/\+(\d+)/)?.[1] || ''}D est.`;
  return label.replace(' sessions', 'D').replace(' min', 'm').replace(' wk', 'W').replace(' mo', 'M');
}

export function validCandle(row) {
  return ['Open', 'High', 'Low', 'Close'].every(k => Number.isFinite(row[k]) && row[k] > 0)
    && row.Low <= Math.min(row.Open, row.Close) && row.High >= Math.max(row.Open, row.Close);
}

function futureLabel(step, interval) {
  const minutes = { '1m': 1, '2m': 2, '5m': 5, '15m': 15, '30m': 30, '1h': 60 }[interval];
  if (minutes) return `+${step * minutes} min`;
  if (interval === '1wk') return `+${step} wk`;
  if (interval === '1mo' || interval === '3mo') return `+${step * (interval === '3mo' ? 3 : 1)} mo`;
  return `+${step * (interval === '5d' ? 5 : 1)} sessions`;
}

function nullSlots(count) {
  return Array.from({ length: count }, () => null);
}

function observedValue(point, candle, field) {
  if (candle && !point.forecastAnchor && !validCandle(point)) return null;
  if (!Number.isFinite(point[field])) return null;
  return point[field];
}

export function buildChartData(prices, settings, predictions, colors) {
  const candle = settings.chartType === 'candle';
  const field = candle || settings.priceField === 'current' ? 'Close'
    : { open: 'Open', high: 'High', low: 'Low', close: 'Close' }[settings.priceField] || 'Close';
  const points = prices.map(row => ({ ...row }));
  const forecast = predictions?.forecast;
  let originIndex = -1, targetOffset = null;
  const wantsForecast = ['both', 'forecast'].includes(settings.overlay);
  const validForecast = forecast && Number.isInteger(forecast.horizon) && forecast.horizon > 0
    && Number.isFinite(forecast.origin_close) && forecast.origin_close > 0
    && Number.isFinite(forecast.estimated_price) && forecast.estimated_price > 0;
  const notes = [];
  if (wantsForecast && field !== 'Close') notes.push('The estimate is a future Close. Choose Close or Candlesticks to display it.');
  if (wantsForecast && field === 'Close' && validForecast && points.length) {
    const originDay = forecast.origin.slice(0, 10);
    originIndex = points.findLastIndex(p => p.Date.slice(0, 10) === originDay);
    if (originIndex < 0) {
      // This is the observed completed Close supplied by the model service,
      // not an invented earlier candle. No OHLC body is drawn for this anchor.
      points.push({ Date: originDay, Close: forecast.origin_close, forecastAnchor: true });
      points.sort((a, b) => a.Date.localeCompare(b.Date));
      originIndex = points.findIndex(p => p.forecastAnchor);
    }
    const laterSessions = new Set(points.slice(originIndex + 1).map(p => p.Date.slice(0, 10))).size;
    if (laterSessions >= forecast.horizon) {
      notes.push('The saved estimate horizon is already in the observed history; no future connector is shown.');
      originIndex = -1;
    } else if (settings.interval === '1d' || settings.interval === 'auto') {
      targetOffset = forecast.horizon - laterSessions;
    } else {
      // Daily model horizon and an intraday/weekly chart have different clocks.
      // Use an explicitly labelled horizon checkpoint, never fake minute bars.
      targetOffset = settings.futureBars;
      notes.push('The final future slot is a daily-horizon checkpoint; spacing to it is schematic.');
    }
  }
  const historyCount = points.length;
  const futureCount = Math.max(settings.futureBars, targetOffset || 0);
  const labels = points.map(p => dateLabel(p.Date));
  const keys = points.map(p => p.Date);
  for (let step = 1; step <= futureCount; step++) {
    labels.push(futureLabel(step, settings.interval));
    keys.push(`future:${step}`);
  }
  const primary = { id: 'observed', label: candle ? 'Candlesticks · OHLC' : `${field} (${settings.interval})`,
    data: points.map(point => observedValue(point, candle, field)).concat(nullSlots(futureCount)),
    yAxisID: 'y', borderColor: colors.price, backgroundColor: colors.price, borderWidth: 2,
    pointRadius: 0, pointHoverRadius: candle ? 0 : 3, pointHitRadius: 6, tension: 0,
    showLine: !candle, fill: false, order: 2 };
  const datasets = [primary];
  let forecastSegment = null;
  if (originIndex >= 0 && targetOffset) {
    const end = historyCount - 1 + targetOffset;
    const values = nullSlots(labels.length);
    values[originIndex] = forecast.origin_close;
    values[end] = forecast.estimated_price;
    labels[end] = `${forecast.origin.slice(0, 10)} +${forecast.horizon} sessions`;
    forecastSegment = { start: originIndex, end, from: forecast.origin_close, to: forecast.estimated_price };
    datasets.push({ id: 'forecast', label: 'Estimated Close · horizon connector', data: values,
      yAxisID: 'y', borderColor: colors.forecast, backgroundColor: colors.forecast,
      borderDash: [6, 5], pointRadius: 3, borderWidth: 2, tension: 0, spanGaps: true, fill: false, order: 1 });
    notes.push(`Dashed line starts at the ${forecast.origin.slice(0, 10)} completed Close and ends at one ${forecast.horizon}-session estimate; intermediate prices are not predicted.`);
  }
  if (['both', 'probability'].includes(settings.overlay)) {
    const byDate = new Map((predictions?.probabilities || []).filter(p => Number.isFinite(p.probability)
      && p.probability >= 0 && p.probability <= 1).map(p => [p.timestamp.slice(0, 10), p.probability * 100]));
    const values = points.map((p, i) => {
      if (points[i + 1]?.Date.slice(0, 10) === p.Date.slice(0, 10)) return null;
      return byDate.get(p.Date.slice(0, 10)) ?? null;
    }).concat(nullSlots(futureCount));
    if (values.some(Number.isFinite)) datasets.push({ id: 'probability', label: 'Daily event probability (%)', data: values,
      yAxisID: 'probability', borderColor: colors.probability, backgroundColor: colors.probability,
      pointRadius: 1, borderWidth: 2, tension: 0, spanGaps: true, fill: false, order: 0 });
  }
  if (candle && points.some(p => !p.forecastAnchor && !validCandle(p))) notes.push('Candles with incomplete or invalid OHLC values are omitted.');
  return { labels, keys, datasets, points, historyCount, futureCount, forecastSegment,
    candle, notes: notes.join(' ') };
}

export function latestViewport(data, visibleBars) {
  const max = data.labels.length - 1;
  return { min: Math.max(0, max - visibleBars + 1), max: Math.max(1, max) };
}

export function preservedViewport(previous, next, bounds, following, visibleBars) {
  if (!previous || following) return latestViewport(next, visibleBars);
  const oldStart = Math.max(0, Math.floor(bounds.min));
  const offset = next.keys.indexOf(previous.keys[oldStart]);
  const min = offset < 0 ? 0 : offset + (bounds.min - oldStart);
  const span = bounds.max - bounds.min;
  const max = Math.min(next.labels.length - 1, min + span);
  return { min: Math.max(0, max - span), max };
}

export function visiblePriceRange(data, bounds) {
  const values = [];
  for (let i = Math.max(0, Math.floor(bounds.min)); i <= Math.min(data.points.length - 1, Math.ceil(bounds.max)); i++) {
    const row = data.points[i];
    if (data.candle && validCandle(row)) values.push(row.Low, row.High);
    else if (!data.candle || row.forecastAnchor) {
      const value = data.datasets[0].data[i];
      if (Number.isFinite(value)) values.push(value);
    }
  }
  const segment = data.forecastSegment;
  if (segment && bounds.max >= segment.start && bounds.min <= segment.end) {
    // Only for axis bounds of the straight connector, never model data points.
    for (const x of [Math.max(bounds.min, segment.start), Math.min(bounds.max, segment.end)]) {
      values.push(segment.from + (segment.to - segment.from) * (x - segment.start) / (segment.end - segment.start));
    }
  }
  if (!values.length) return {};
  const min = Math.min(...values), max = Math.max(...values);
  const padding = Math.max((max - min) * .08, Math.abs(max) * .001, .01);
  return { min: min - padding, max: max + padding };
}

export const marketDrawingPlugin = {
  id: 'market-drawing',
  beforeDatasetsDraw(chart) {
    const data = chart.$marketData;
    if (!data || !chart.chartArea) return;
    const { ctx, chartArea: area, scales: { x, y } } = chart;
    ctx.save();
    ctx.beginPath(); ctx.rect(area.left, area.top, area.right - area.left, area.bottom - area.top); ctx.clip();
    const boundary = x.getPixelForValue(data.historyCount - .5);
    if (boundary < area.right) {
      ctx.fillStyle = chart.$colors.future;
      ctx.fillRect(Math.max(area.left, boundary), area.top, area.right - Math.max(area.left, boundary), area.bottom - area.top);
    }
    if (data.candle && chart.isDatasetVisible(0)) {
      const width = Math.max(1, Math.min(24, Math.abs(x.getPixelForValue(1) - x.getPixelForValue(0)) * .65));
      for (let i = Math.max(0, Math.floor(x.min)); i <= Math.min(data.points.length - 1, Math.ceil(x.max)); i++) {
        const row = data.points[i];
        if (!validCandle(row)) continue;
        const px = x.getPixelForValue(i), open = y.getPixelForValue(row.Open), close = y.getPixelForValue(row.Close);
        ctx.fillStyle = ctx.strokeStyle = row.Close >= row.Open ? chart.$colors.up : chart.$colors.down;
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(px, y.getPixelForValue(row.High)); ctx.lineTo(px, y.getPixelForValue(row.Low)); ctx.stroke();
        ctx.fillRect(px - width / 2, Math.min(open, close), width, Math.max(1, Math.abs(close - open)));
      }
    }
    ctx.restore();
  },
};
