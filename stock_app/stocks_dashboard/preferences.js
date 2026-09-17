// Read before styles load so a saved dark theme never flashes light on reload.
globalThis.StockPreferences = (() => {
  const key = 'stock-dashboard.preferences.v1';
  const defaults = { ticker: 'AAPL', range: '1m', interval: 'auto', priceField: 'current',
    theme: 'light', overlay: 'none', chartType: 'line', autoRefresh: true,
    showFullHistory: false, futureBars: 10, visibleBars: 60 };
  const choices = {
    range: ['1d', '1w', '1m', '3m', '6m', 'ytd', '1y', '2y', '5y', '10y', 'all'],
    interval: ['auto', '1m', '2m', '5m', '15m', '30m', '1h', '1d', '5d', '1wk', '1mo', '3mo'],
    priceField: ['close', 'open', 'high', 'low', 'current'], theme: ['light', 'dark'],
    overlay: ['both', 'forecast', 'probability', 'none'], chartType: ['line', 'candle'],
  };
  function clean(input) {
    const result = { ...defaults };
    if (!input || typeof input !== 'object') return result;
    for (const [name, values] of Object.entries(choices)) {
      if (values.includes(input[name])) result[name] = input[name];
    }
    if (typeof input.ticker === 'string' && /^[A-Z0-9.^=-]{1,10}$/.test(input.ticker)) result.ticker = input.ticker;
    for (const name of ['autoRefresh', 'showFullHistory']) {
      if (typeof input[name] === 'boolean') result[name] = input[name];
    }
    if ([5, 10, 20].includes(input.futureBars)) result.futureBars = input.futureBars;
    if (Number.isInteger(input.visibleBars) && input.visibleBars >= 2 && input.visibleBars <= 100000) result.visibleBars = input.visibleBars;
    return result;
  }
  function read() {
    try { return clean(JSON.parse(globalThis.localStorage?.getItem(key) || 'null')); }
    catch { return { ...defaults }; }
  }
  function save(settings) {
    try { globalThis.localStorage?.setItem(key, JSON.stringify(clean(settings))); }
    catch { /* Private browsing/storage restrictions must not stop the chart. */ }
  }
  const initial = read();
  globalThis.document?.documentElement.classList.toggle('dark', initial.theme === 'dark');
  return { read, save };
})();
