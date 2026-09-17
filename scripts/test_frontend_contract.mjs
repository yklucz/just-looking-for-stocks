// DOM/Chart adapter test of the actual dashboard module. This is not visual QA.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const html = fs.readFileSync('stock_app/stocks_dashboard/index.html', 'utf8');
const source = fs.readFileSync('stock_app/stocks_dashboard/script.js', 'utf8');
const chartHelpers = await import(`data:text/javascript,${encodeURIComponent(fs.readFileSync('stock_app/stocks_dashboard/chart-data.js', 'utf8'))}`);
const preferencesSource = fs.readFileSync('stock_app/stocks_dashboard/preferences.js', 'utf8');
class Element {
  constructor(tagName='div') { this.tagName=tagName; this.textContent=''; this.value=''; this.style={}; this.dataset={}; this.children=[]; this.listeners={}; this.classList={add(){},remove(){},toggle(){}}; }
  set innerHTML(value) { this.children=[]; this.content=value; }
  get innerHTML() { return this.content || ''; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
  setAttribute(name,value) { this[name]=value; }
  removeEventListener() {}
  appendChild(child) { this.insertBefore(child,null); }
  insertBefore(child,before) { child.remove(); child.parent=this; const at=before?this.children.indexOf(before):this.children.length; this.children.splice(at,0,child); }
  remove() { if(this.parent) this.parent.children.splice(this.parent.children.indexOf(this),1); this.parent=null; }
  getContext() { return {}; }
  querySelector() { return new Element(); }
}
const elements = Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const table = new Element(), loader = new Element();
const document = {
  documentElement:new Element(),
  getElementById(id) { assert.ok(elements[id], `Removed/missing DOM node ${id}`); return elements[id]; },
  querySelector(selector) {
    if(selector==='#historyTable tbody') return table;
    if(selector==='[data-chart-loader="price"]') return loader;
    throw Error(`Unexpected selector ${selector}`);
  },
  querySelectorAll() { return []; },
  createElement(tagName) { return new Element(tagName); },
};
const charts=[];
class Chart {
  static register() {}
  constructor(ctx,config) { this.data=config.data; this.options=config.options; this.updates=[]; this.update('none'); charts.push(this); }
  update(mode) { this.scales={x:{...this.options.scales.x}, y:{...this.options.scales.y}}; this.updates.push(mode); }
  destroy() { throw Error('Dashboard must keep its chart instance'); }
  resetZoom() {}
  zoom() {}
}
const prices = [{Date:'2026-09-08',Open:100,High:103,Low:99,Close:102,Volume:100},
                {Date:'2026-09-09',Open:102,High:105,Low:101,Close:104,Volume:200}];
const requested=[];
let invalidProbability=false;
let nvdaReady=false, trainingPolls=0;
let historyFailure=false, historyGate=null;
async function fetch(url, options={}) {
  requested.push(url);
  const u=new URL(url,'http://localhost');
  let payload,ok=true;
  if(u.pathname==='/api/predict') {
    if(u.searchParams.get('ticker')==='MSFT') { ok=false; payload={code:'MODEL_NOT_AVAILABLE',error:'No saved model'}; }
    else if(u.searchParams.get('ticker')==='NVDA' && !nvdaReady) { ok=false; payload={code:'MODEL_NOT_AVAILABLE',error:'No saved model'}; }
    else payload={timestamp:'2026-09-09',prediction:{probability_up:invalidProbability?1.2:.63,horizon:5,event_threshold:.002},
                  signal:{action:'LONG',decision_threshold:.5},model:{type:'xgboost',trained_until:'2023-12-04',version:'abcdef1234567890',stale:true},market:{stale:false}};
  } else if(u.pathname==='/api/prediction-chart') payload={forecast_status:'ready',
    probabilities:[{timestamp:'2026-09-08',probability:.55},{timestamp:'2026-09-09',probability:.63}],
    forecast:{origin:'2026-09-09',origin_close:104,horizon:5,estimated_price:106},
    evaluation:{classification:{status:'ready',samples:30,pending_samples:5,origin_start:'2026-07-20',origin_end:'2026-09-02',metrics:{accuracy:.6,roc_auc:.52,brier_score:.24},baseline:{accuracy:.65}},
      regression:{status:'ready',samples:30,pending_samples:5,origin_start:'2026-07-20',origin_end:'2026-09-02',metrics:{price_mae:4,price_rmse:5,price_mape_percent:3.7},baseline:{price_mae:3},beats_baseline_mae:false}}};
  else if(u.pathname==='/api/train') {
    assert.equal(options.method,'POST');
    assert.equal(options.headers['Content-Type'],'application/json');
    const ticker=JSON.parse(options.body).ticker;
    if(ticker==='NVDA') {
      assert.match(elements.predictionStatus.textContent,/Training XGBoost/);
      nvdaReady=++trainingPolls>1;
      payload={status:nvdaReady?'ready':'training'};
    } else payload={status:'failed',error:'No saved model: training data unavailable'};
  }
  else if(u.pathname==='/api/history') {
    if(historyGate) await historyGate;
    if(historyFailure) { ok=false; payload={error:'History temporarily unavailable'}; }
    else payload={prices,interval:'1d'};
  }
  else if(u.pathname==='/api/info') payload={symbol:'AAPL',name:'Apple',currentPrice:110,quoteTimestamp:'2026-09-09T20:00:00+00:00'};
  else payload=[{symbol:'AAPL',name:'Apple'}];
  return {ok,json:async()=>payload};
}
const saved=new Map();
const context={document,Chart,fetch,URLSearchParams,Date,console,setTimeout,clearTimeout,...chartHelpers,
  localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)}, getComputedStyle:()=>({getPropertyValue:()=> '#888'})};
vm.createContext(context);
vm.runInContext(preferencesSource, context);
const runtime=vm.runInContext(`(async()=>{${source.replace(/^import .*;\n/, '')}\n return {loadAll,runSearch,state,latestView};})()`,context);
const app=await runtime;
assert.equal(elements.priceFieldSelect.value,'current');
assert.equal(charts[0].$marketData.currentPrice.price,110);
assert.equal(elements.predictionValue.textContent,'63.0%');
assert.match(elements.predictionStatus.textContent,/stale/);
assert.equal(elements.predictionHorizon.textContent,'5 daily candles');
assert.match(elements.forecastDetails.textContent,/106/);
assert.match(elements.classificationAccuracy.textContent,/60.0%/);
assert.match(elements.regressionAccuracy.textContent,/did not beat/);
assert.equal(table.children.length,2);
assert.ok(charts.length >= 1);
assert.deepEqual(Array.from(charts[0].data.datasets[0].data.slice(0,2)),[102,110]);
assert.equal(charts[0].data.datasets.length,1);
assert.equal(charts[0].$marketData.futureCount,0);
assert.equal(charts[0].options.plugins.legend.display,false);
await elements.overlaySelect.listeners.change({target:{value:'both'}});
assert.equal(charts[0].data.datasets.length,3);
assert.equal(charts.length,1);
assert.equal(elements.error.textContent,'');
assert.equal(elements.quickPicks.children[0].tagName,'button');
assert.equal(elements.quickPicks.children[0].textContent,'AAPL');
assert.equal(elements.quickPicks.children[0].title,'AAPL — Apple');
assert.equal(table.children[0].children[1].textContent,'102.00');
assert.equal(table.children[0].children[0].textContent,'2026-09-09');
assert.equal(elements.toggleHistoryRows['aria-expanded'],'false');
elements.toggleHistoryRows.listeners.click();
assert.equal(elements.toggleHistoryRows['aria-expanded'],'true');
assert.equal(elements.toggleHistoryRows.textContent,'Show summary');
elements.toggleHistoryRows.listeners.click();
prices[0].Open=null;
await app.loadAll();
assert.equal(table.children[1].children[1].textContent,'—');
prices[0].Open=100;
assert.equal(charts.at(-1).data.datasets.length,3);
assert.deepEqual(Array.from(charts.at(-1).data.datasets[1].data.filter(Number.isFinite)),[104,106]);
assert.equal(charts.at(-1).data.datasets[1].data[6],106);
assert.equal(charts.at(-1).data.datasets[2].yAxisID,'probability');
for (const [value,count] of [['probability',2],['forecast',2],['none',1],['both',3]]) {
  await elements.overlaySelect.listeners.change({target:{value}});
  assert.equal(charts.at(-1).data.datasets.length,count);
}
await app.runSearch('apple');
assert.equal(elements.symbolList.children[0].value,'AAPL');
elements.priceFieldSelect.value='open'; elements.intervalSelect.value='1h';
await app.loadAll();
// The forecast is explicitly a future Close estimate, so it is hidden when
// the user switches the primary chart to Open/High/Low.
assert.deepEqual(Array.from(charts.at(-1).data.datasets[0].data.slice(0,2)),[100,102]);
assert.match(elements.overlayStatus.textContent,/Choose Close/);
assert.ok(requested.filter(u=>u.startsWith('/api/predict')).every(u=>!u.includes('interval')&&!u.includes('price_field')));
elements.tickerInput.value='MSFT';
await app.loadAll();
assert.equal(elements.predictionValue.textContent,'—');
assert.match(elements.predictionStatus.textContent,/No saved model/);
assert.equal(table.children.length,2);
elements.tickerInput.value='NVDA';
await app.loadAll();
assert.equal(trainingPolls,2);
assert.equal(elements.predictionValue.textContent,'63.0%');
await app.loadAll();
assert.equal(trainingPolls,2,'Saved model must be reused without training again');
// Preferences and display switches do not refetch history or rebuild Chart.js.
let requestCount=requested.length;
elements.themeToggle.listeners.click();
elements.chartTypeSelect.listeners.change({target:{value:'candle'}});
assert.equal(charts.length,1);
assert.equal(requested.length,requestCount);
assert.equal(charts[0].data.datasets[0].showLine,false);
assert.equal(elements.priceFieldSelect.disabled,true);
const stored=JSON.parse([...saved.values()][0]);
assert.equal(stored.theme,'dark');
assert.equal(stored.chartType,'candle');
assert.equal(stored.ticker,'NVDA');
const restored={localStorage:context.localStorage,document};
vm.createContext(restored); vm.runInContext(preferencesSource,restored);
assert.equal(restored.StockPreferences.read().theme,'dark');
assert.equal(restored.StockPreferences.read().chartType,'candle');
// Visible values stay present throughout a delayed refresh; a same-timestamp
// revision replaces one point and the new candle adds exactly one observation.
elements.priceFieldSelect.value='close';
elements.chartTypeSelect.listeners.change({target:{value:'line'}});
app.state.priceField='close';
const chart=charts[0], tableRow=table.children[0];
const oldClose=chart.data.datasets[0].data[1];
const priorCount=chart.$marketData.historyCount;
let releaseHistory;
historyGate=new Promise(resolve=>{releaseHistory=resolve;});
prices[1].Close=104.5;
prices.push({Date:'2026-09-10',Open:104.5,High:107,Low:104,Close:106,Volume:300});
const refreshing=app.loadAll(true);
assert.equal(table.children[0],tableRow);
assert.equal(chart.data.datasets[0].data[1],oldClose);
assert.equal(loader.style.display,'none');
releaseHistory(); await refreshing; historyGate=null;
assert.equal(chart.$marketData.historyCount,priorCount+1);
assert.equal(chart.data.datasets[0].data[1],104.5);
assert.equal(chart.data.datasets[0].data[2],106);
assert.equal(table.children[1],tableRow);
assert.equal(charts.length,1);
assert.ok(chart.updates.every(mode=>mode==='none'));
// An unsuccessful background refresh must not clear the last good series.
historyFailure=true;
await app.loadAll(true);
assert.equal(chart.data.datasets[0].data[2],106);
assert.equal(loader.style.display,'none');
assert.match(elements.error.textContent,/History temporarily unavailable/);
historyFailure=false;
// Draft ticker text must not become a selected ticker during auto-refresh.
elements.tickerInput.value='UNSUBMITTED';
await app.loadAll(true);
assert.equal(app.state.ticker,'NVDA');
assert.ok(!requested.some(url=>url.includes('UNSUBMITTED')));
elements.tickerInput.value='AAPL'; invalidProbability=true;
await app.loadAll();
assert.equal(elements.predictionValue.textContent,'—');
assert.match(elements.predictionStatus.textContent,/Invalid model probability/);
console.log('Frontend contract: overlays, persistence, candlesticks, in-place refresh, failures, search and automatic training passed');
// A standalone daily Close anchor is not one of the two candles in Max zoom.
app.state.chartPredictions={forecast:{origin:'2026-09-09',origin_close:104,horizon:5,estimated_price:106}};
app.state.chartInterval='1m';
app.state.historyData=[{...prices[0],Date:'2026-09-09 15:58'},{...prices[1],Date:'2026-09-09 15:59'}];
elements.priceFieldSelect.value='current'; elements.priceFieldSelect.listeners.change();
elements.maxPriceZoom.listeners.click();
assert.match(elements.chartViewStatus.textContent,/2 visible candles/);
