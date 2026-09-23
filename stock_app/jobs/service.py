"""Registered application services. No model selection, training, or ledger rules here."""
from pathlib import Path
import os

import pandas as pd

from stock_app.research.config import INITIAL_SYMBOLS, CONTEXT_SYMBOLS, SECTOR_ETFS
from stock_app.research.runtime import ROOT, ResearchRuntime
from stock_app.research.calendar import latest_completed_session, utc_timestamp
from .core import JobDefinition, JobRunner, PreparationError, UnsafeExecution
from .health import job_health, source_freshness


def registry(store):
    try:
        symbols = store.get('settings', 'universe')['symbols']
    except KeyError:
        symbols = INITIAL_SYMBOLS
    jobs = [JobDefinition(f'refresh:{symbol}', 'refresh', symbol)
            for symbol in dict.fromkeys([*CONTEXT_SYMBOLS, *symbols])]
    for symbol in symbols:
        jobs.append(JobDefinition(f'forecast:{symbol}', 'forecast', symbol))
        for task in ('binary', 'regression'):
            jobs.append(JobDefinition(f'experiment:{symbol}:{task}', 'experiment', symbol, 'monthly', task))
    return jobs


class JobService:
    def __init__(self, root=None, *, clock=utc_timestamp, downloader=None):
        root = Path(root or os.environ.get('STOCK_RESEARCH_ROOT', ROOT / 'artifacts' / 'research'))
        # Operational CLI construction must never import/activate legacy models.
        self.runtime = ResearchRuntime(root, import_legacy=False)
        self.clock = clock
        self.downloader = downloader or self.download
        self.runner = JobRunner(self.runtime, registry(self.runtime.store),
                                {'refresh': self.refresh, 'forecast': self.forecast,
                                 'experiment': self.experiment}, clock=clock)

    @staticmethod
    def download(symbol):
        import yfinance as yf
        return yf.Ticker(symbol).history(period='max', interval='1d', auto_adjust=False, actions=True)

    def refresh(self, job, context):
        frame = self.downloader(job.symbol)
        now = utc_timestamp(self.clock())
        # Provider/validation failures here are known to have no persisted effects.
        adjusted, _ = self.runtime.data._validate_and_adjust(frame, now, 'XNYS')
        if adjusted.index[-1] < latest_completed_session(now - pd.Timedelta(minutes=30)):
            raise PreparationError('Provider response is missing the latest required completed session')
        context.begin_effects({'symbol': job.symbol, 'source': 'yahoo', 'downloaded_at': now.isoformat()})
        meta = self.runtime.data.ingest(job.symbol, frame, now=now)
        context.store.update(context.run_id, outputs={'snapshot_id': meta['id']})
        if meta['status'] != 'valid':
            raise UnsafeExecution('Snapshot was quarantined')
        self.runtime.store.put('datasets', {**meta, 'downloaded_at': meta['created_at']})
        from stock_app.research.ledger import resolve_forecasts
        resolve_forecasts(self.runtime.store, job.symbol, self.runtime.data.load(meta['id']),
                          snapshot_id=meta['id'], now=now)
        return {'snapshot_id': meta['id'], 'last_session': meta['last_session'],
                'freshness': source_freshness(self.runtime.data, job.symbol, now)}

    def inputs(self, job, context):
        mapping = {}
        sources = []
        for symbol in dict.fromkeys([job.symbol, 'SPY', 'QQQ', SECTOR_ETFS.get(job.symbol, 'SPY')]):
            freshness = source_freshness(self.runtime.data, symbol, self.clock())
            sources.append(freshness)
            context.store.update(context.run_id, inputs={'sources': sources})
            if freshness['status'] != 'fresh':
                raise PreparationError(f"Required source {symbol} is {freshness['status']}; {freshness['reason']}")
            mapping[symbol] = freshness['snapshot_id']
        return mapping

    def forecast(self, job, context):
        mapping = self.inputs(job, context)
        context.begin_effects({'snapshots': mapping})
        result = self.runtime.issue_daily(job.symbol, snapshots=mapping)
        outputs = {'forecast_ids': result['issued'], 'revision_ids': result.get('revision_ids', [])}
        context.store.update(context.run_id, outputs=outputs)
        if result['errors']:
            raise UnsafeExecution('Forecast service reported partial failures')
        return {**outputs, 'snapshots': mapping}

    def experiment(self, job, context):
        mapping = self.inputs(job, context)
        identity = 'operational-' + context.run_id
        context.begin_effects({'snapshots': mapping, 'job_id': identity})
        # Dedicated ID, not legacy enqueue(): it merges distinct active logical runs.
        self.runtime.store.put('jobs', {'id': identity, 'kind': 'experiment', 'symbol': job.symbol,
                                      'state': 'queued', 'snapshots': mapping, 'progress': {},
                                      'parameters': {'task': job.task, 'include_gru': False}})
        context.store.update(context.run_id, outputs={'job_id': identity})
        # The operational runner already owns the shared worker lock.
        result = self.runtime._run_job(identity)
        if result['state'] != 'completed':
            raise UnsafeExecution('Experiment paused or failed; use legacy review/resume after reconciliation')
        from stock_app.research.registry import Registry
        research_runs = [r for r in Registry(self.runtime.store).list('runs') if r['execution_key'] == 'job:' + identity]
        return {'job_id': identity, 'snapshots': mapping,
                'research_run_id': research_runs[0]['id'] if research_runs else None,
                'research_spec_id': research_runs[0]['spec_id'] if research_runs else None,
                'candidate': result.get('progress', {}).get('candidate')}

    def status(self):
        return {'registered_jobs': list(self.runner.definitions), 'schedules': self.runner.store.schedules(),
                'runs': self.runner.store.runs(), 'attempts': self.runner.store.attempts(),
                'heartbeat': self.runner.store.heartbeat()}

    def health(self):
        return job_health(self.runner)
