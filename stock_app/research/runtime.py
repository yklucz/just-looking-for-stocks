"""One local worker, durable jobs, immutable inputs, and explicit activation."""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import fcntl
from contextlib import contextmanager
from threading import Event, Lock, Thread

import pandas as pd

from .config import INITIAL_SYMBOLS, SECTOR_ETFS, CONTEXT_SYMBOLS, UNIVERSE_VERSION
from .data import DataRepository
from .store import ResearchStore, utcnow

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
_runtimes = {}
_runtime_lock = Lock()


def get_runtime():
    root = Path(os.environ.get('STOCK_RESEARCH_ROOT', ROOT / 'artifacts' / 'research')).resolve()
    with _runtime_lock:
        if str(root) not in _runtimes:
            _runtimes[str(root)] = ResearchRuntime(root)
        return _runtimes[str(root)]


class ResearchRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.store = ResearchStore(self.root / 'research.sqlite3')
        self.data = DataRepository(self.root / 'datasets')
        self._wake = Event()
        self._start_lock = Lock()
        self._thread = None
        try:
            self.store.get('settings', UNIVERSE_VERSION)
        except KeyError:
            self.store.put('settings', {'id': UNIVERSE_VERSION, 'market': 'SPY',
                                       'sector_mappings': SECTOR_ETFS, 'context_symbols': CONTEXT_SYMBOLS,
                                       'note': 'Fixed present-day universe; selection and survivorship bias apply'})
        self.import_legacy()

    def import_legacy(self):
        manifest = Path(os.environ.get('STOCK_MODEL_MANIFEST', ROOT / 'config/prediction_models.json'))
        if not manifest.exists():
            return
        saved = json.loads(manifest.read_text())
        for binding, entry in saved.get('bindings', {}).items():
            symbol, model_type = binding.split(':', 1)
            task = 'regression' if model_type == 'xgboost_regressor' else 'binary'
            identity = 'legacy-' + entry['fingerprint'][:32]
            try:
                self.store.get('models', identity)
                continue
            except KeyError:
                pass
            artifact = Path(entry['artifact'])
            if not artifact.is_absolute():
                artifact = ROOT / artifact
            metadata_file = artifact / 'metadata.json'
            metadata = json.loads(metadata_file.read_text()) if metadata_file.exists() else {}
            record = {'id': identity, 'symbol': symbol, 'task': task, 'state': 'candidate',
                      'artifact': str(artifact), 'artifact_sha256': entry.get('model_sha256'),
                      'metadata': {'legacy': True, 'binding': entry, 'contract': metadata.get('contract', {}),
                                   'note': 'Imported legacy version; no prospective evidence inferred'}}
            self.store.put('models', record)
            if self.store.active(symbol, task) is None:
                self.store.set_active(identity)

    def start(self):
        with self._start_lock:
            if self._thread is None or not self._thread.is_alive():
                with self.worker_lease() as owned:
                    if owned:
                        self.store.recover_jobs()
                self._thread = Thread(target=self._loop, name='research-worker', daemon=True)
                self._thread.start()
        self._wake.set()

    def submit(self, kind, symbol, task='binary', *, include_gru=False, key=None):
        if not isinstance(symbol, str):
            raise ValueError('Research symbol must be a string')
        symbol = symbol.strip().upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9.^=_-]{0,31}', symbol):
            raise ValueError('Invalid research symbol')
        if (not isinstance(kind, str) or not isinstance(task, str)
                or kind not in {'refresh', 'experiment', 'forecast', 'company_context'}
                or task not in {'binary', 'regression'}):
            raise ValueError('Unsupported research job or task')
        if type(include_gru) is not bool:
            raise ValueError('include_gru must be boolean')
        result = self.store.enqueue(kind, symbol, {'task': task, 'include_gru': include_gru}, key=key)
        self._wake.set()
        return result

    def control(self, identity, action):
        job = self.store.get('jobs', identity)
        if action == 'cancel':
            if job['state'] in {'queued', 'paused', 'running'}:
                job = self.store.update('jobs', identity, state='cancelling' if job['state'] == 'running' else 'cancelled')
        elif action == 'resume':
            if job['state'] not in {'paused', 'cancelled', 'failed'}:
                raise ValueError('Only paused, cancelled or failed jobs can resume')
            job = self.store.update('jobs', identity, state='queued', error=None)
            self._wake.set()
        else:
            raise ValueError('Unsupported job action')
        return job

    def schedule(self, now=None):
        import exchange_calendars as xcals
        clock = pd.Timestamp(now or utcnow())
        day = clock.tz_convert('America/New_York').tz_localize(None).normalize()
        cal = xcals.get_calendar('XNYS', start=day - pd.Timedelta(days=20), end=day + pd.Timedelta(days=5))
        session = cal.date_to_session(day, direction='previous')
        if clock < cal.session_close(session) + pd.Timedelta(minutes=30):
            session = cal.previous_session(session)
        key = session.date().isoformat()
        try:
            symbols = self.store.get('settings', 'universe')['symbols']
        except KeyError:
            symbols = INITIAL_SYMBOLS
        for symbol in CONTEXT_SYMBOLS:
            self.submit('refresh', symbol, key=f'refresh:{symbol}:{key}')
        for symbol in symbols:
            self.submit('refresh', symbol, key=f'refresh:{symbol}:{key}')
            # A daily forecast is queued behind the refresh. An old origin will be labelled replay.
            self.submit('forecast', symbol, key=f'forecast:{symbol}:{key}')
            for task in ('binary', 'regression'):
                self.submit('experiment', symbol, task, key=f'monthly:{symbol}:{task}:{key[:7]}')

    def _loop(self):
        while True:
            try:
                if os.environ.get('STOCK_RESEARCH_SCHEDULE', '1') == '1':
                    self.schedule()
                jobs = self.store.list('jobs', state='queued')
                # Market refresh and issuance take precedence over new training.
                jobs.sort(key=lambda job: (job['kind'] == 'experiment', job['created_at']))
                if jobs:
                    result = self.run_job(jobs[0]['id'])
                    if result.get('state') != 'queued':
                        continue
            except Exception:
                logger.exception('Research worker failed; retained durable state')
            self._wake.wait(60)
            self._wake.clear()

    def refresh(self, symbol):
        meta = self.data.refresh(symbol)
        self.store.put('datasets', {**meta, 'start': meta.get('first_session'), 'end': meta.get('last_session'),
                                    'downloaded_at': meta['created_at'], 'error': '; '.join(meta.get('errors', []))})
        latest = self.data.latest(symbol)
        if latest.get('status') != 'valid':
            raise ValueError('; '.join(latest.get('errors', ['No valid data'])))
        from .ledger import resolve_forecasts
        resolve_forecasts(self.store, symbol, self.data.load(latest['id']), snapshot_id=latest['id'])
        return latest

    def inputs(self, symbol, snapshots=None):
        mapping = dict(snapshots or {})
        freshness = {}
        for item in dict.fromkeys([symbol, 'SPY', 'QQQ', SECTOR_ETFS.get(symbol, 'SPY')]):
            if not item or item in mapping:
                continue
            try:
                meta = self.data.latest(item)
            except KeyError:
                meta = self.refresh(item)
            if meta['status'] != 'valid':
                raise ValueError(f'No valid snapshot for {item}')
            mapping[item] = meta['id']
            freshness[item] = {'snapshot_id': meta['id'], 'stale': bool(meta.get('stale'))}
        frames = {item: self.data.load(identity) for item, identity in mapping.items()}
        frames[symbol].attrs['data_freshness'] = freshness
        return frames[symbol], {key: value for key, value in frames.items()}, mapping

    @contextmanager
    def worker_lease(self):
        with (self.root / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def run_job(self, identity):
        with self.worker_lease() as owned:
            if not owned:
                return self.store.get('jobs', identity)
            return self._run_job(identity)

    def _run_job(self, identity):
        job = self.store.get('jobs', identity)
        if job['state'] != 'queued':
            return job
        self.store.update('jobs', identity, state='running', started_at=utcnow(), error=None)
        try:
            if job['kind'] == 'refresh':
                result = self.refresh(job['symbol'])
            elif job['kind'] == 'forecast':
                result = self.issue_daily(job['symbol'])
            elif job['kind'] == 'company_context':
                from .events import download_free_context
                result = download_free_context(job['symbol'])
                for row in result['events'] + result['fundamentals']:
                    self.store.put('events', row)
                if result['status'] == 'failed':
                    raise ValueError(str(result['errors']))
            else:
                from .experiments import run_experiment
                history, contexts, mapping = self.inputs(job['symbol'], job.get('snapshots'))
                self.store.update('jobs', identity, snapshots=mapping)
                result = run_experiment(history, ticker=job['symbol'], output=self.root / 'experiments' / identity,
                                        contexts=contexts, task=job['parameters']['task'],
                                        cancelled=lambda: self.store.get('jobs', identity)['state'] == 'cancelling',
                                        include_gru=job['parameters'].get('include_gru', False))
                if result.get('status') == 'completed' and result.get('candidate'):
                    candidate = result['candidate']
                    path = Path(candidate['path'] if isinstance(candidate, dict) else candidate)
                    model_id = hashlib.sha256(path.read_bytes()).hexdigest()
                    with self.store.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        try:
                            self.store._get(db, 'models', model_id)
                        except KeyError:
                            self.store._put(db, 'models', {'id': model_id, 'symbol': job['symbol'], 'task': job['parameters']['task'],
                                              'state': 'candidate', 'artifact': str(path), 'artifact_sha256': model_id,
                                              'metadata': {'job_id': identity, 'snapshots': mapping,
                                                           'candidate': candidate, 'evaluation_kind': 'historical_replay'}})
            state = result.get('status', 'completed') if isinstance(result, dict) else 'completed'
            if state not in {'paused', 'cancelled'}:
                state = 'completed'
            if self.store.get('jobs', identity)['state'] == 'cancelling':
                state = 'cancelled'
            return self.store.update('jobs', identity, state=state, progress=result, finished_at=utcnow())
        except Exception as exc:
            logger.exception('Research job %s failed', identity)
            return self.store.update('jobs', identity, state='failed', error=str(exc), finished_at=utcnow())

    def issue_daily(self, symbol):
        from .ledger import issue_forecast, resolve_forecasts
        from .inference import model_payload
        history, contexts, mapping = self.inputs(symbol)
        snapshot = hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()
        issued, errors = [], []
        models = [model for model in self.store.list('models', symbol=symbol) if model['state'] in {'active', 'shadow'}]
        for model in models:
            try:
                payload = model_payload(model, history, contexts)
                record = issue_forecast(self.store, symbol=symbol, model_id=model['id'], snapshot_id=snapshot,
                                        origin=payload.pop('origin_time'), payload={**payload, 'snapshots': mapping})
                issued.append(record['id'])
            except (ValueError, KeyError, RuntimeError, OSError) as exc:
                errors.append({'model_id': model['id'], 'error': str(exc)})
        resolve_forecasts(self.store, symbol, history, snapshot_id=snapshot)
        return {'issued': issued, 'errors': errors}
