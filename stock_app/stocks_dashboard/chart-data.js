// Display geometry only. Blank future slots are not candles or model outputs.
export function dateLabel(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}:\d{2}))?/.exec(String(value));
  if (!match) return value;
  const date = `${match[1]}/${match[2]}/${match[3]}`;
  return match[4] ? `${date} ${match[4]}` : date;
}

export function axisLabel(label) {
  if (label.endsWith(' completed Close')) return 'Daily Close';
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
  if (point.forecastAnchor || (candle && !validCandle(point))) return null;
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
  const validForecast = forecast && typeof forecast.origin === 'string'
    && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(forecast.origin) && Number.isInteger(forecast.horizon) && forecast.horizon > 0
    && Number.isFinite(forecast.origin_close) && forecast.origin_close > 0
    && Number.isFinite(forecast.estimated_price) && forecast.estimated_price > 0;
  const notes = [];
  if (wantsForecast && field !== 'Close') notes.push('The estimate is a future Close. Choose Close or Candlesticks to display it.');
  if (wantsForecast && field === 'Close' && validForecast && points.length) {
    const originDay = forecast.origin.slice(0, 10);
    originIndex = ['1d', 'auto'].includes(settings.interval)
      ? points.findLastIndex(p => p.Date.slice(0, 10) === originDay) : -1;
    if (originIndex < 0) {
      // This is the observed completed Close supplied by the model service,
      // not an invented earlier candle. No OHLC body is drawn for this anchor.
      points.push({ Date: `${originDay} 23:59`, Close: forecast.origin_close, forecastAnchor: true });
      points.sort((a, b) => a.Date.localeCompare(b.Date));
      originIndex = points.findIndex(p => p.forecastAnchor);
    }
    const laterSessions = new Set(points.slice(originIndex + 1).filter(p => !p.forecastAnchor).map(p => p.Date.slice(0, 10))).size;
    if (['5d', '1wk', '1mo', '3mo'].includes(settings.interval)) {
      notes.push('Switch to a daily or intraday interval for the five-session price connector; aggregated candles use a different time scale.');
      points.splice(originIndex, 1);
      originIndex = -1;
    } else if (laterSessions >= forecast.horizon) {
      notes.push('The saved estimate horizon is already in the observed history; no future connector is shown.');
      originIndex = -1;
    } else if (settings.interval === '1d' || settings.interval === 'auto') {
      targetOffset = forecast.horizon - laterSessions;
    } else {
      // Daily model horizon and an intraday/weekly chart have different clocks.
      // Use an explicitly labelled horizon checkpoint, never fake minute bars.
      targetOffset = forecast.horizon - laterSessions;
      notes.push('The daily-horizon checkpoint has schematic spacing, independent of the blank right-space setting.');
    }
  }
  const historyCount = points.length;
  const futureCount = wantsForecast ? Math.max(settings.futureBars, targetOffset || 0) : 0;
  const labels = points.map(p => p.forecastAnchor ? `${p.Date.slice(0, 10)} completed Close` : dateLabel(p.Date));
  const keys = points.map(p => p.forecastAnchor ? `anchor:${p.Date}` : p.Date);
  for (let step = 1; step <= futureCount; step++) {
    labels.push(futureLabel(step, settings.interval));
    keys.push(`future:${step}`);
  }
  const primary = { id: 'observed', label: candle ? 'Candlesticks · OHLC' : `${field} (${settings.interval})`,
    data: points.map(point => observedValue(point, candle, field)).concat(nullSlots(futureCount)),
    yAxisID: 'y', borderColor: colors.price, backgroundColor: colors.price, borderWidth: 2,
    pointRadius: 0, pointHoverRadius: candle ? 0 : 3, pointHitRadius: 6, tension: 0,
    showLine: !candle, spanGaps: true, fill: false, order: 2 };
  const datasets = [primary];
  let forecastSegment = null;
  let forecastRange = null;
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
    if (settings.showUncertainty && Number.isFinite(forecast.lower_price) && Number.isFinite(forecast.upper_price)
        && forecast.lower_price > 0 && forecast.upper_price >= forecast.lower_price) {
      forecastRange = {end, lower:forecast.lower_price, upper:forecast.upper_price};
      for (const [name, value] of [['Lower',forecast.lower_price],['Upper',forecast.upper_price]]) {
        const bounds = nullSlots(labels.length); bounds[end] = value;
        datasets.push({id:`interval-${name}`,label:`${name} · nominal 80% range`,data:bounds,
          yAxisID:'y',borderColor:colors.forecast,backgroundColor:colors.forecast,pointRadius:4,showLine:false});
      }
      notes.push('Optional nominal 80% endpoint range. Actual historical coverage is reported in Research; coverage is not guaranteed.');
    }
  }
  if (['both', 'probability'].includes(settings.overlay) && ['1d', 'auto'].includes(settings.interval)) {
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
  if (['both', 'probability'].includes(settings.overlay) && !['1d', 'auto'].includes(settings.interval)) {
    notes.push('Daily event probabilities are shown on the daily chart only, so end-of-day estimates are not assigned to earlier intraday or aggregated candles.');
  }
  const latest = prices.findLast(p => Number.isFinite(p.Close) && p.Close > 0);
  const quote = settings.quote;
  const currentPrice = settings.priceField === 'current' && !candle && latest ? {
    price: Number.isFinite(quote?.price) && quote.price > 0 ? quote.price : latest.Close,
    source: Number.isFinite(quote?.price) && quote.price > 0 ? 'Latest quote' : 'Latest candle Close',
    timestamp: Number.isFinite(quote?.price) && quote.price > 0 ? quote.timestamp : latest.Date,
  } : null;
  if (currentPrice) {
    const pointIndex = points.findLastIndex(p => !p.forecastAnchor && Number.isFinite(p.Close) && p.Close > 0);
    currentPrice.pointIndex = pointIndex;
    // Only the displayed endpoint changes. Keep raw OHLC and model anchors intact.
    primary.data[pointIndex] = currentPrice.price;
    primary.label = `Current price (${settings.interval})`;
    primary.pointRadius = primary.data.map((_, index) => index === pointIndex ? 3 : 0);
    if (currentPrice.source === 'Latest quote') labels[pointIndex] = 'Latest quote';
    notes.push(`Current line: historical Close samples ending at ${currentPrice.source.toLowerCase()} ${currentPrice.price.toFixed(2)}${currentPrice.timestamp ? ` · ${currentPrice.timestamp}` : ' · quote time unavailable'}. The quote uses the last display slot; underlying OHLC and the forecast origin are unchanged. Yahoo data may be delayed.`);
  }
  if (candle && points.some(p => !p.forecastAnchor && !validCandle(p))) notes.push('Candles with incomplete or invalid OHLC values are omitted.');
  return { labels, keys, datasets, points, historyCount, futureCount, forecastSegment, forecastRange,
    candle, currentPrice, notes: notes.join(' ') };
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
  if (data.currentPrice) values.push(data.currentPrice.price);
  if (data.forecastRange && bounds.min <= data.forecastRange.end && bounds.max >= data.forecastRange.end) {
    values.push(data.forecastRange.lower, data.forecastRange.upper);
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
    if (data.currentPrice && chart.isDatasetVisible(0)) {
      const py = y.getPixelForValue(data.currentPrice.price);
      ctx.strokeStyle = chart.$colors.price; ctx.lineWidth = 1;
      ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(area.left, py); ctx.lineTo(area.right, py); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = chart.$colors.price; ctx.font = '12px Helvetica Neue, sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(`${data.currentPrice.source} ${data.currentPrice.price.toFixed(2)}`, area.right - 6, Math.max(area.top + 14, py - 6));
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
