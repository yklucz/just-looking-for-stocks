// Numeric/display invariants, independent of the browser and external data.
import fs from 'node:fs';
import assert from 'node:assert/strict';
import vm from 'node:vm';
const { buildChartData, dateLabel, axisLabel, latestViewport, preservedViewport, visiblePriceRange,
  validCandle, marketDrawingPlugin } = await import(`data:text/javascript,${encodeURIComponent(fs.readFileSync('stock_app/stocks_dashboard/chart-data.js','utf8'))}`);
const settings={priceField:'close', chartType:'line', interval:'1d', overlay:'both',futureBars:10};
const colors={price:'black',forecast:'amber',probability:'purple',up:'green',down:'red',future:'tint'};
const rows=[{Date:'2026-09-08',Open:100,High:105,Low:99,Close:103},
  {Date:'2026-09-09',Open:103,High:107,Low:101,Close:104}];
const predictions={forecast:{origin:'2026-09-09T00:00:00-04:00',origin_close:104,horizon:5,estimated_price:108},
  probabilities:[{timestamp:'2026-09-09',probability:.65}]};
let data=buildChartData(rows,settings,predictions,colors);
assert.equal(data.labels.length,12);
assert.equal(data.labels[6],'2026-09-09 +5 sessions');
assert.deepEqual(data.datasets[0].data.slice(2),Array(10).fill(null));
assert.deepEqual(data.datasets[1].data.filter(Number.isFinite),[104,108]);
assert.equal(data.datasets[1].data[1],data.datasets[0].data[1]);
assert.equal(data.datasets[1].data[6],108);
assert.equal(data.datasets[2].yAxisID,'probability');
assert.equal(data.datasets[2].data[1],65);
assert.deepEqual(latestViewport(data,12),{min:0,max:11});
assert.equal(dateLabel('2026-09-09T09:30:00-04:00'),'2026/09/09 09:30');
for (const [interval,label] of [['1m','+1 min'],['5m','+5 min'],['1h','+60 min'],['1d','+1 sessions'],['1wk','+1 wk'],['3mo','+3 mo']]) {
  const result=buildChartData(rows,{...settings,interval},predictions,colors);
  assert.equal(result.labels[result.historyCount],label);
  if (['1wk','3mo'].includes(interval)) {
    assert.equal(result.forecastSegment,null);
    assert.match(result.notes,/aggregated/);
  } else {
    assert.equal(result.datasets[1].data.filter(Number.isFinite).length,2);
    if (interval!=='1d') assert.match(result.notes,/schematic/);
  }
}
const revised=[...rows,{Date:'2026-09-10',Open:104,High:200,Low:100,Close:190}];
const newData=buildChartData(revised,settings,predictions,colors);
assert.equal(newData.datasets[1].data[1],104,'Do not rebase yesterday\'s model estimate on today\'s live quote');
assert.equal(newData.datasets[1].data[6],108,'Appending an observed session must not push the forecast horizon forward');
assert.equal(newData.datasets[1].data[2],null);
const missingOrigin=buildChartData(rows.slice(-1),settings,{...predictions,
  forecast:{...predictions.forecast,origin:'2026-09-08',origin_close:103}},colors);
assert.equal(missingOrigin.points[0].forecastAnchor,true);
assert.equal(missingOrigin.datasets[1].data[0],103);
assert.equal(missingOrigin.points[0].Open,undefined,'Never fabricate an OHLC candle for a Close-only anchor');
const historicalBounds={min:0,max:1};
assert.deepEqual(preservedViewport(data,newData,historicalBounds,false,10),historicalBounds);
assert.equal(preservedViewport(data,newData,historicalBounds,true,10).max,newData.labels.length-1);
const removedOld=buildChartData(revised.slice(1),settings,predictions,colors);
assert.equal(preservedViewport(newData,removedOld,{min:1,max:3},false,3).min,0,'Keep the same dates after a moving history window removes its first row');
const candle=buildChartData(rows,{...settings,chartType:'candle',priceField:'open'},predictions,colors);
assert.equal(candle.datasets[0].showLine,false);
assert.equal(candle.datasets[1].data[1],104,'Candlestick mode can still show a Close forecast');
const range=visiblePriceRange(candle,{min:0,max:1});
assert.ok(range.min<99 && range.max>107,'Auto scale must include full wicks');
assert.equal(validCandle({...rows[0],High:null}),false);
assert.equal(validCandle({...rows[0],Low:101}),false);
assert.equal(validCandle(rows[0]),true);
assert.equal(buildChartData([{...rows[0],High:null}],{...settings,chartType:'candle'},null,colors).datasets[0].data[0],null);
const drawn=[], ctx={save(){},restore(){},beginPath(){},rect(){},clip(){},moveTo(x,y){drawn.push(['move',x,y]);},
  lineTo(x,y){drawn.push(['line',x,y]);},stroke(){},fillRect(x,y,w,h){drawn.push(['body',x,y,w,h]);}};
marketDrawingPlugin.beforeDatasetsDraw({$marketData:candle,$colors:colors,ctx,
  chartArea:{left:0,right:200,top:0,bottom:200},
  scales:{x:{min:0,max:1,getPixelForValue:i=>i*30+10},y:{getPixelForValue:v=>200-v}},isDatasetVisible:()=>true});
assert.ok(drawn.some(d=>d[0]==='line' && d[2]===101),'First wick ends at the supplied Low=99');
assert.ok(drawn.some(d=>d[0]==='body' && d[4]===3),'First body height equals Close-Open');
const preferenceSource=fs.readFileSync('stock_app/stocks_dashboard/preferences.js','utf8');
for (const storage of [{getItem(){throw Error('denied');},setItem(){throw Error('denied');}},
  {getItem:()=>'{bad json'}, {getItem:()=>JSON.stringify({theme:'oops',chartType:'invalid',visibleBars:-3,futureBars:1000000})}]) {
  const sandbox={localStorage:storage}; vm.createContext(sandbox); vm.runInContext(preferenceSource,sandbox);
  assert.equal(sandbox.StockPreferences.read().theme,'light');
  assert.equal(sandbox.StockPreferences.read().visibleBars,60);
  sandbox.StockPreferences.save(settings);
}
console.log('Chart data: forecast anchors, future gaps, interval labels, viewport continuity, OHLC drawing and preference recovery passed');

// Missing model anchors must not become observations or bend the price line.
assert.equal(missingOrigin.datasets[0].data[0],null,'Forecast-only anchors must not be inserted into historical prices');
const intradayRows=[{...rows[1],Date:'2026-09-09 12:00',Close:103},
  {...rows[1],Date:'2026-09-09 15:59',Close:103.5}];
const intraday=buildChartData(intradayRows,{...settings,interval:'1m'},predictions,colors);
assert.equal(intraday.points[intraday.forecastSegment.start].forecastAnchor,true,
  'Daily completed Close must not be attached to a different minute Close');
const wide=buildChartData(intradayRows,{...settings,interval:'1m',futureBars:20},predictions,colors);
assert.equal(intraday.forecastSegment.end,wide.forecastSegment.end,'Changing blank right space must not move the forecast');
const invalid=buildChartData(rows,settings,{forecast:{...predictions.forecast,origin:null}},colors);
assert.equal(invalid.forecastSegment,null,'Malformed forecast dates must not crash history');
const quoted=buildChartData(rows,{...settings,priceField:'current',quote:{price:110,timestamp:'2026-09-09T20:00:00Z'}},predictions,colors);
assert.equal(quoted.currentPrice.price,110);
assert.deepEqual(quoted.datasets[0].data.slice(0,2),[103,110],'Current line must end at the latest quote');
assert.equal(quoted.points[1].Close,104,'Underlying OHLC must retain its actual Close');
assert.equal(rows[1].Close,104,'Input candles must not be mutated');
assert.match(quoted.datasets[0].label,/Current price/);
assert.equal(quoted.labels[1],'Latest quote');
assert.equal(buildChartData(rows,{...settings,priceField:'close',quote:{price:110}},predictions,colors).datasets[0].data[1],104,'Close selection must restore the actual candle Close');
assert.equal(quoted.forecastSegment.from,104,'A quote must never rebase model predictions');
assert.ok(visiblePriceRange(quoted,{min:0,max:11}).max>110,'Default chart scale includes current quote');
const fallback=buildChartData(rows,{...settings,priceField:'current'},null,colors);
assert.equal(fallback.currentPrice.price,104);
assert.equal(fallback.currentPrice.source,'Latest candle Close');
console.log('Current price and forecast regression cases passed');

assert.equal(axisLabel('2026-09-09 completed Close'),'Daily Close');

const simple=buildChartData(rows,{...settings,overlay:'none'},predictions,colors);
assert.equal(simple.datasets.length,1);
assert.equal(simple.futureCount,0,'Price-only charts must not waste width on empty forecast slots');
assert.equal(simple.labels.length,rows.length);
const uncertain = buildChartData(rows, {...settings, showUncertainty:true}, {...predictions,
  forecast:{...predictions.forecast,lower_price:90,upper_price:120}}, colors);
assert.equal(uncertain.forecastRange.lower,90);
assert.equal(uncertain.forecastRange.upper,120);
const uncertaintyBounds=visiblePriceRange(uncertain,{min:0,max:uncertain.labels.length-1});
assert.ok(uncertaintyBounds.min<90 && uncertaintyBounds.max>120);
assert.equal(buildChartData(rows,settings,{...predictions,
  forecast:{...predictions.forecast,lower_price:90,upper_price:120}},colors).forecastRange,null);
