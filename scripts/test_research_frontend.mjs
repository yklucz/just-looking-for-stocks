// Behavioral DOM tests of the production Research module; no external packages.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const {createResearchApp, csvExport} = await import(`data:text/javascript,${encodeURIComponent(fs.readFileSync('stock_app/stocks_dashboard/research.js', 'utf8'))}`);
class Element {
  constructor(tag='div') { this.tagName=tag; this.children=[]; this.listeners={}; this.attributes={}; this.value=''; this.textContent=''; this.hidden=false; this.disabled=false; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children=children; this.textContent=''; }
  setAttribute(key,value) { this.attributes[key]=String(value); }
  addEventListener(key,listener) { this.listeners[key]=listener; }
  focus() { this.focused=true; }
  get allText() { return this.textContent + this.children.map(child=>child.allText).join(' '); }
}
const ids=['filters','symbol','model-id','evaluation-kind','period-from','period-to','refresh','download-json','download-csv','status','error','action-status','job-form','job-symbol','job-kind','job-task','job-submit','records','record-count',...['datasets','jobs','models','forecasts'].map(tab=>`tab-${tab}`)];
const elements=Object.fromEntries(ids.map(id=>[id,new Element()]));
elements['job-symbol'].value='AAPL'; elements['job-kind'].value='experiment'; elements['job-task'].value='binary';
const doc={hidden:false,getElementById:id=>{assert.ok(elements[id],id); return elements[id];},createElement:tag=>new Element(tag),listeners:{},addEventListener(key,fn){this.listeners[key]=fn;}};
const data={datasets:[{id:'d1',symbol:'AAPL',status:'ready',rows:50,stale:true,error:'<img src=x onerror=alert(1)>'}],jobs:[{id:'j1',kind:'experiment',symbol:'AAPL',state:'running',progress:{stage:'evaluation'}}],models:[{id:'m1',symbol:'AAPL',state:'candidate',qualification:{eligible:false,reasons:['Insufficient baseline evidence']}},{id:'m2',symbol:'AAPL',state:'shadow',qualification:{eligible:true,metrics:{brier_score:.24}}}],forecasts:[{id:'f1',symbol:'AAPL',model_id:'m2',kind:'prospective',state:'pending',payload:{probability_up:.6}}]};
let failure=false; const calls=[],downloads=[]; let scheduled;
const fetch=async(url,options={})=>{calls.push({url,options}); if(options.method==='POST') return {ok:true,json:async()=>({id:'new'})}; const resource=url.split('?')[0].split('/').at(-1); return {ok:!failure,json:async()=>failure?{error:'Temporarily unavailable'}:{items:data[resource]}};};
const app=createResearchApp({document:doc,fetch,download:(...args)=>downloads.push(args),setTimeout:fn=>{scheduled=fn;return 1;},clearTimeout:()=>{}});
await app.start();
assert.match(elements.records.allText,/AAPL/);
assert.match(elements.records.allText,/Stale/);
assert.match(elements.records.allText,/<img src=x onerror=alert\(1\)>/);
assert.equal(elements.records.children[0].tagName,'article','Data must be rendered as DOM, never server HTML');
await app.selectTab('models');
assert.equal(elements['tab-models'].attributes['aria-selected'],'true');
const descendants=node=>[node,...node.children.flatMap(descendants)];
let buttons=descendants(elements.records).filter(node=>node.tagName==='button');
assert.equal(buttons.filter(node=>node.textContent==='Activate').length,1,'Only eligible models can be activated');
assert.match(elements.records.allText,/Insufficient baseline evidence/);
data.models[0].metadata={candidate:{calibration_label_end:'2026-03-31'}};
data.models[1].metadata={contract:{fit_dates:{validation:{label_end:'2025-12-12'}}}};
await app.refresh();
assert.match(elements.records.allText,/Training cutoff 2026-03-31/);
assert.match(elements.records.allText,/Training cutoff 2025-12-12/);
await buttons.find(node=>node.textContent==='Nominate for shadow').listeners.click();
assert.ok(calls.some(call=>call.url==='/api/research/models/m1/nominate'&&call.options.method==='POST'));
await app.selectTab('jobs');
buttons=descendants(elements.records).filter(node=>node.tagName==='button');
await buttons.find(node=>node.textContent==='Cancel').listeners.click();
assert.ok(calls.some(call=>call.url==='/api/research/jobs/j1/cancel'));
data.jobs.push({id:'j2',kind:'experiment',symbol:'AAPL',state:'completed',progress:{status:'completed',folds:[{comparisons:{training_prior:{mean_improvement:-.01,lower:-.05,upper:.03}}}]}});
await app.refresh(); assert.match(elements.records.allText,/Baseline not beaten/);
data.jobs[1].progress.folds[0].comparisons.training_prior.mean_improvement=.01;
await app.refresh(); assert.match(elements.records.allText,/Historical improvement inconclusive/);
data.jobs[1].progress.folds[0].comparisons.training_prior.lower=.001;
await app.refresh(); assert.match(elements.records.allText,/prospective evidence is still required/);
await elements['job-form'].listeners.submit({preventDefault(){}});
const submission=calls.find(call=>call.url==='/api/research/jobs'&&call.options.method==='POST');
assert.deepEqual(JSON.parse(submission.options.body),{kind:'experiment',symbol:'AAPL',task:'binary',include_gru:false});
await app.selectTab('forecasts');
assert.match(elements.records.allText,/Pending/);
assert.match(elements.records.allText,/outcome unavailable/i);
elements.symbol.value='MSFT'; elements['model-id'].value='m2'; elements['evaluation-kind'].value='prospective';
elements['period-from'].value='2026-01-01'; elements['period-to'].value='2026-06-30';
await elements.filters.listeners.submit({preventDefault(){}});
assert.match(calls.at(-1).url,/symbol=MSFT&model_id=m2&kind=prospective/);
assert.match(calls.at(-1).url,/from=2026-01-01&to=2026-06-30/);
await app.selectTab('jobs'); assert.match(calls.at(-1).url,/from=2026-01-01&to=2026-06-30/);
await app.selectTab('models'); assert.doesNotMatch(calls.at(-1).url,/from=|to=/);
await app.selectTab('forecasts');
let beforeInvalid=calls.length; elements['period-to'].value='2025-12-31';
await elements.filters.listeners.submit({preventDefault(){}});
assert.equal(calls.length,beforeInvalid); assert.match(elements.error.textContent,/From date must be on or before/);
elements['period-to'].value='2026-06-30';
await elements.filters.listeners.submit({preventDefault(){}});
const retained=elements.records.children[0]; failure=true; await app.refresh();
assert.equal(elements.records.children[0],retained,'Refresh errors retain the last successful data');
assert.match(elements.error.textContent,/Temporarily unavailable/);
elements['download-json'].listeners.click(); assert.match(downloads.at(-1)[0],/f1/);
assert.match(csvExport([{id:'=formula',payload:{note:'a,"b"'}}]),/"'=formula"/,'CSV neutralizes spreadsheet formulas');
let count=calls.length; doc.hidden=true; await scheduled(); assert.equal(calls.length,count,'Hidden pages do not poll');
doc.hidden=false; failure=false; await doc.listeners.visibilitychange(); assert.ok(calls.length>count);
elements['tab-datasets'].listeners.keydown({key:'ArrowRight',preventDefault(){}});
assert.equal(elements['tab-jobs'].focused,true);
app.stop();
console.log('Research frontend: tab navigation, filters, safe rendering, actions, exports, retained errors and visible polling passed');
