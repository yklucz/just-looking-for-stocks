const TABS = ['datasets', 'jobs', 'models', 'forecasts'];
const LABELS = {datasets:'Data', jobs:'Experiments', models:'Models', forecasts:'Forecast history'};
const display = value => value === undefined || value === null || value === '' ? 'Unavailable' : String(value);
const readable = value => display(value).replaceAll('_', ' ');

function historicalComparison(report) {
  const comparisons = (report?.folds || []).flatMap(fold => Object.values(fold.comparisons || {}));
  if (!comparisons.length) return 'Historical baseline comparisons unavailable.';
  if (comparisons.some(result => Number.isFinite(result.mean_improvement) && result.mean_improvement <= 0)) {
    return 'Baseline not beaten in at least one historical comparison. Review the fold results before drawing conclusions.';
  }
  if (comparisons.some(result => !Number.isFinite(result.lower) || result.lower <= 0 || !Number.isFinite(result.mean_improvement))) {
    return 'Historical improvement inconclusive: the comparisons do not consistently establish an advantage.';
  }
  return 'Historical comparisons favor the selected procedure; prospective evidence is still required for this candidate.';
}

export function csvExport(items) {
  const keys = [...new Set(items.flatMap(item => Object.keys(item)))];
  const cell = value => {
    let content = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (/^[\s]*[=+@-]/u.test(content)) content = `'${content}`;
    return `"${content.replaceAll('"', '""')}"`;
  };
  return [keys.map(cell).join(','), ...items.map(item => keys.map(key => cell(item[key])).join(','))].join('\r\n');
}

function browserDownload(content, type, filename) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const link = document.createElement('a');
  link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function createResearchApp({document: doc, fetch: request, download = browserDownload,
  setTimeout: schedule = globalThis.setTimeout, clearTimeout: unschedule = globalThis.clearTimeout}) {
  const el = id => doc.getElementById(id);
  const state = {tab:'datasets', items:[], filters:{symbol:'', model_id:'', kind:'', from:'', to:''}, sequence:0, timer:null, stopped:false, fingerprint:null};
  const node = (tag, text, className) => {
    const element = doc.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const note = (parent, text, danger = false) => parent.append(node('p', text, danger ? 'record-note record-error' : 'record-note'));
  function facts(parent, values) {
    const list = node('dl', undefined, 'record-facts');
    for (const [label, value] of values) {
      const pair = node('div'); pair.append(node('dt', label), node('dd', display(value))); list.append(pair);
    }
    parent.append(list);
  }
  function details(parent, title, value) {
    const disclosure = node('details');
    disclosure.append(node('summary', title), node('pre', JSON.stringify(value, null, 2)));
    parent.append(disclosure);
  }
  async function api(url, options = {}) {
    const response = await request(url, options);
    let payload;
    try { payload = await response.json(); }
    catch { throw new Error('The research service did not return a readable response'); }
    if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }
  async function action(button, url, success, body = {}) {
    if (button.disabled) return;
    button.disabled = true;
    el('action-status').textContent = 'Submitting request…';
    try {
      await api(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      el('action-status').textContent = success;
      await refresh();
    } catch (error) { el('action-status').textContent = `Request failed: ${error.message}`; }
    finally { button.disabled = false; }
  }
  function actionButton(parent, label, url, success) {
    const button = node('button', label); button.type = 'button';
    button.addEventListener('click', () => action(button, url, success)); parent.append(button);
  }
  function record(item) {
    const article = node('article', undefined, 'research-record');
    const heading = node('div', undefined, 'record-heading');
    const title = state.tab === 'jobs' ? `${display(item.symbol)} · ${readable(item.kind)}` : display(item.symbol);
    const status = item.state || item.status;
    heading.append(node('h2', title), node('span', readable(status), 'record-state')); article.append(heading);
    if (state.tab === 'datasets') {
      facts(article, [['Rows',item.rows], ['Start',item.start], ['End',item.end], ['Downloaded',item.downloaded_at]]);
      if (item.stale) note(article, 'Stale data — refresh before relying on this snapshot.');
      if (item.error) note(article, display(item.error), true);
      details(article, 'Data provenance', item);
    } else if (state.tab === 'jobs') {
      facts(article, [['Job ID',item.id], ['Created',item.created_at], ['Task',readable(item.task || item.parameters?.task)], ['Progress',typeof item.progress === 'object' ? item.progress?.stage || item.progress?.status : item.progress]]);
      if (item.progress?.aggregate) facts(article, Object.entries(item.progress.aggregate).map(([key,value]) => [readable(key), typeof value === 'number' ? value.toFixed(4) : value]));
      if (item.kind === 'experiment' && (status === 'completed' || item.progress?.status === 'completed')) {
        note(article, historicalComparison(item.progress));
        note(article, 'Historical research comparison. This report is not a prospective track record.');
      }
      if (item.error) note(article, display(item.error), true);
      const actions = node('div', undefined, 'record-actions');
      if (['queued','running','cancelling','cancel_requested'].includes(status)) actionButton(actions, 'Cancel', `/api/research/jobs/${encodeURIComponent(item.id)}/cancel`, 'Cancellation requested. The worker will stop at a safe checkpoint.');
      if (['failed','cancelled','interrupted','paused'].includes(status)) actionButton(actions, 'Resume', `/api/research/jobs/${encodeURIComponent(item.id)}/resume`, 'Resume requested.');
      article.append(actions); details(article, 'Progress and report', item);
    } else if (state.tab === 'models') {
      const qualification = item.qualification || {};
      facts(article, [['Model ID',item.id], ['Task',readable(item.task)],
        ['Training cutoff',item.metadata?.candidate?.calibration_label_end || item.metadata?.contract?.fit_dates?.validation?.label_end],
        ['Resolved outcomes',qualification.resolved]]);
      if (item.metadata?.legacy) note(article, 'Legacy model — provenance or qualification evidence may be incomplete.');
      note(article, qualification.eligible ? 'Eligible for manual activation. Eligibility does not promise future performance.' : 'Not eligible for activation. No proven advantage is implied.');
      if (Array.isArray(qualification.reasons)) qualification.reasons.forEach(reason => note(article, display(reason)));
      if (!qualification.metrics || Object.keys(qualification.metrics).length === 0) note(article, 'Evaluation metrics unavailable.');
      const actions = node('div', undefined, 'record-actions');
      const base = `/api/research/models/${encodeURIComponent(item.id)}`;
      if (status === 'candidate') actionButton(actions, 'Nominate for shadow', `${base}/nominate`, 'Model nominated for shadow evaluation.');
      if (qualification.eligible === true && status !== 'active') actionButton(actions, 'Activate', `${base}/activate`, 'Model activated by your request.');
      if (status === 'retired') actionButton(actions, 'Rollback to this model', `${base}/rollback`, 'Previous model restored by your request.');
      article.append(actions); details(article, 'Qualification, metrics and provenance', item);
    } else {
      facts(article, [['Model ID',item.model_id], ['Origin',item.origin], ['Issued',item.issued_at], ['Evaluation kind',readable(item.kind)]]);
      if (status === 'pending') note(article, 'Pending — outcome unavailable until the forecast horizon has elapsed and market data arrives.');
      else if (status === 'missing_data') note(article, 'Outcome unavailable: required market data is missing.');
      else if (status === 'corrected') note(article, 'Corrected market data changed this outcome; inspect the record history.');
      if (!item.outcome || Object.keys(item.outcome).length === 0) {
        if (status !== 'pending' && status !== 'missing_data') note(article, 'Outcome unavailable.');
      }
      details(article, 'Forecast and outcome', item);
    }
    return article;
  }
  function render(items) {
    const fingerprint = JSON.stringify([state.tab, items]);
    if (fingerprint !== state.fingerprint) {
      el('records').replaceChildren(...(items.length ? items.map(record) : [node('p', `No ${LABELS[state.tab].toLowerCase()} records match these filters. Start a job below or adjust your filters.`, 'empty-records')]));
      state.fingerprint = fingerprint;
    }
    el('record-count').textContent = `${items.length} ${items.length === 1 ? 'record' : 'records'} · ${LABELS[state.tab]}`;
    el('download-csv').disabled = !items.length; el('download-json').disabled = !items.length;
  }
  function scheduleRefresh() {
    unschedule(state.timer);
    if (!state.stopped) state.timer = schedule(async () => {
      if (!doc.hidden) await refresh(); else scheduleRefresh();
    }, 10000);
  }
  async function refresh() {
    const sequence = ++state.sequence;
    el('status').textContent = 'Updating records…';
    const params = new URLSearchParams();
    if (state.filters.symbol) params.set('symbol', state.filters.symbol);
    if (state.tab === 'forecasts') {
      if (state.filters.model_id) params.set('model_id', state.filters.model_id);
      if (state.filters.kind) params.set('kind', state.filters.kind);
    }
    if (state.tab === 'forecasts' || state.tab === 'jobs') {
      if (state.filters.from) params.set('from', state.filters.from);
      if (state.filters.to) params.set('to', state.filters.to);
    }
    try {
      const payload = await api(`/api/research/${state.tab}?${params}`);
      if (sequence !== state.sequence || state.stopped) return;
      if (!Array.isArray(payload.items)) throw new Error('The server returned an invalid record list.');
      state.items = payload.items; render(state.items);
      el('error').hidden = true; el('error').textContent = '';
      el('status').textContent = `Updated ${new Date().toLocaleTimeString()}. Refreshes every 10 seconds while this page is visible.`;
    } catch (error) {
      if (sequence !== state.sequence || state.stopped) return;
      el('error').hidden = false; el('error').textContent = `Could not refresh: ${error.message}. Last loaded records remain visible.`;
      if (state.fingerprint === null) {
        el('record-count').textContent = `No records loaded · ${LABELS[state.tab]}`;
        el('error').textContent = `Could not load records: ${error.message}. Use Refresh to try again.`;
      }
      el('status').textContent = 'Refresh unsuccessful. You can retry with Refresh.';
    } finally { if (sequence === state.sequence) scheduleRefresh(); }
  }
  async function selectTab(tab) {
    if (!TABS.includes(tab)) return;
    if (state.tab !== tab) { state.items = []; state.fingerprint = null; }
    state.tab = tab;
    for (const name of TABS) {
      el(`tab-${name}`).setAttribute('aria-selected', String(name === tab));
      el(`tab-${name}`).setAttribute('tabindex', name === tab ? '0' : '-1');
    }
    el('records').setAttribute('aria-labelledby', `tab-${tab}`);
    render(state.items); return refresh();
  }
  for (const [index, tab] of TABS.entries()) {
    el(`tab-${tab}`).addEventListener('click', () => selectTab(tab));
    el(`tab-${tab}`).addEventListener('keydown', event => {
      const next = {ArrowRight:(index+1)%TABS.length, ArrowLeft:(index+TABS.length-1)%TABS.length, Home:0, End:TABS.length-1}[event.key];
      if (next === undefined) return;
      event.preventDefault(); el(`tab-${TABS[next]}`).focus(); void selectTab(TABS[next]);
    });
  }
  el('filters').addEventListener('submit', event => {
    event.preventDefault();
    const from = el('period-from').value;
    const to = el('period-to').value;
    if (from && to && from > to) {
      el('error').hidden = false;
      el('error').textContent = 'From date must be on or before the To date. Your previous filters remain applied.';
      return;
    }
    state.filters = {symbol:el('symbol').value.trim().toUpperCase(), model_id:el('model-id').value.trim(), kind:el('evaluation-kind').value, from, to};
    return refresh();
  });
  el('refresh').addEventListener('click', refresh);
  el('job-form').addEventListener('submit', event => {
    event.preventDefault();
    const symbol = el('job-symbol').value.trim().toUpperCase();
    if (!symbol) { el('action-status').textContent = 'Enter a ticker before starting a job.'; return; }
    return action(el('job-submit'), '/api/research/jobs', 'Job submitted. Open Experiments to follow its progress.',
      {kind:el('job-kind').value, symbol, task:el('job-task').value, include_gru:false});
  });
  for (const type of ['csv','json']) el(`download-${type}`).addEventListener('click', () => {
    if (!state.items.length) return;
    download(type === 'csv' ? csvExport(state.items) : JSON.stringify(state.items, null, 2),
      type === 'csv' ? 'text/csv;charset=utf-8' : 'application/json', `research-${state.tab}.${type}`);
  });
  doc.addEventListener('visibilitychange', () => {
    if (!doc.hidden && !state.stopped) return refresh();
    unschedule(state.timer);
  });
  return {start:refresh, refresh, selectTab, stop:() => {state.stopped = true; unschedule(state.timer);}, state};
}

if (typeof document !== 'undefined') createResearchApp({document, fetch:globalThis.fetch.bind(globalThis)}).start();
