"""Finite, externally triggered runner. No Flask, daemon, or process-time leases."""
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Callable

import pandas as pd

from stock_app.research.calendar import latest_completed_session, session_close, sessions_between, utc_timestamp
from .store import JobStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobDefinition:
    name: str
    kind: str
    symbol: str
    cadence: str = 'daily'
    task: str = 'binary'
    max_attempts: int = 3
    retry_seconds: int = 60
    max_retry_seconds: int = 900

    def __post_init__(self):
        if self.cadence not in {'daily', 'monthly'}:
            raise ValueError('Unsupported cadence')
        if min(self.max_attempts, self.retry_seconds, self.max_retry_seconds) < 1:
            raise ValueError('Retry limits must be positive')

    def latest(self, now):
        session = latest_completed_session(utc_timestamp(now) - pd.Timedelta(minutes=30))
        if self.cadence == 'monthly':
            session = sessions_between(session.replace(day=1), session)[0]
        return session_close(session) + pd.Timedelta(minutes=30)

    def slots(self, previous, now):
        latest = self.latest(now)
        if previous is None:
            return [latest]
        previous = utc_timestamp(previous)
        if previous >= latest:
            return []
        sessions = sessions_between(previous.normalize(), latest.normalize())
        if self.cadence == 'monthly':
            sessions = sessions[[day == sessions_between(day.replace(day=1), day)[0] for day in sessions]]
        return [session_close(day) + pd.Timedelta(minutes=30) for day in sessions
                if previous < session_close(day) + pd.Timedelta(minutes=30) <= latest]


class UnsafeExecution(RuntimeError):
    """An operation may have committed effects; operator reconciliation is required."""


class PreparationError(RuntimeError):
    """Safe, application-owned failure reason (never raw provider text)."""


@dataclass
class ExecutionContext:
    store: JobStore
    run_id: str
    clock: Callable

    def begin_effects(self, inputs: dict):
        """Durably fence non-transactional effects BEFORE calling an unsafe service."""
        run = self.store.update(self.run_id, phase='effects', inputs=inputs)
        self.store.attempt(run)
        self.store.beat(utc_timestamp(self.clock()).isoformat(), state='running', run_id=self.run_id)


class JobRunner:
    def __init__(self, runtime, definitions, handlers, *, clock=utc_timestamp):
        self.runtime = runtime
        self.store = JobStore(runtime.store)
        self.definitions = {job.name: job for job in definitions}
        self.handlers = handlers
        self.clock = clock

    def now(self):
        return utc_timestamp(self.clock())

    @staticmethod
    def identity(name, scheduled_at):
        return hashlib.sha256(f'{name}|{utc_timestamp(scheduled_at).isoformat()}'.encode()).hexdigest()

    def enqueue(self, definition, scheduled_at, *, status='queued', reason=None, db=None):
        if db is None:
            with self.runtime.store.connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                return self.enqueue(definition, scheduled_at, status=status, reason=reason, db=connection)
        at = utc_timestamp(scheduled_at).isoformat()
        identity = self.identity(definition.name, at)
        row = db.execute('SELECT document FROM operational_runs WHERE id=?', (identity,)).fetchone()
        if row:
            return json.loads(row[0])
        run = dict(id=identity, idempotency_key=identity, name=definition.name,
                   kind=definition.kind, symbol=definition.symbol, task=definition.task,
                   scheduled_at=at, created_at=self.now().isoformat(), status=status,
                   attempt=0, started_at=None, finished_at=None, next_attempt_at=at,
                   phase='preparing', inputs={}, outputs={}, error=None, reason=reason)
        if status == 'skipped':
            run['finished_at'] = self.now().isoformat()
        legacy_key = (f'monthly:{definition.symbol}:{definition.task}:{at[:7]}'
                      if definition.kind == 'experiment' else f'{definition.kind}:{definition.symbol}:{at[:10]}')
        legacy = db.execute('SELECT job_id FROM job_keys WHERE key=?', (legacy_key,)).fetchone()
        if legacy:
            old = self.runtime.store._get(db, 'jobs', legacy[0])
            run.update(status='succeeded' if old['state'] == 'completed' else 'blocked',
                       outputs={'legacy_job_id': old['id']},
                       reason='Adopted legacy scheduled identity; incomplete legacy work requires reconciliation',
                       finished_at=old.get('finished_at'), started_at=old.get('started_at'))
        self.store.save_run(db, run)
        return run

    def schedule(self):
        """Transactional cursor plus explicit coalesced history for every missed slot."""
        now = self.now()
        with self.runtime.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for definition in self.definitions.values():
                row = db.execute('SELECT document FROM operational_schedules WHERE name=?',
                                 (definition.name,)).fetchone()
                saved = json.loads(row[0]) if row else {}
                slots = definition.slots(saved.get('last_scheduled_at'), now)
                if not slots:
                    continue
                latest = slots[-1]
                for slot in slots:
                    self.enqueue(definition, slot, db=db,
                                 status='queued' if slot == latest else 'skipped',
                                 reason=None if slot == latest else 'Coalesced after downtime; current-vintage inputs only')
                # A pending old refresh/forecast must not produce another latest-origin issuance.
                rows = db.execute("SELECT document FROM operational_runs WHERE name=? AND scheduled_at<? "
                                  "AND status IN ('queued','retry')", (definition.name, latest.isoformat())).fetchall()
                for row in rows:
                    run = json.loads(row[0])
                    run.update(status='skipped', finished_at=now.isoformat(), reason='Superseded by latest scheduled slot')
                    self.store.save_run(db, run)
                saved.update(name=definition.name, kind=definition.kind, cadence=definition.cadence,
                             last_scheduled_at=latest.isoformat(), registered_at=saved.get('registered_at', now.isoformat()))
                db.execute('INSERT OR REPLACE INTO operational_schedules VALUES(?,?)',
                           (definition.name, json.dumps(saved)))

    def _failed(self, run, error):
        definition = self.definitions[run['name']]
        blocked = run['phase'] == 'effects' or isinstance(error, UnsafeExecution)
        status = 'blocked' if blocked else ('failed' if run['attempt'] >= definition.max_attempts else 'retry')
        delay = min(definition.max_retry_seconds, definition.retry_seconds * 2 ** (run['attempt'] - 1))
        # Exception text from providers can contain credentials/URLs. Persist a safe class and guidance.
        reason = ('Effects may have committed; inspect artifacts and reconcile manually. No automatic retry.'
                  if blocked else 'Pre-effect failure; inspect source health and provider availability.')
        if isinstance(error, PreparationError):
            reason = str(error)
        result = self.store.update(run['id'], status=status, finished_at=self.now().isoformat(),
                                   error={'type': type(error).__name__, 'reason': reason},
                                   next_attempt_at=(self.now() + pd.Timedelta(seconds=delay)).isoformat())
        self.store.attempt(result)
        self._log(result)
        return result

    def recover(self):
        # Only call while owning the same OS lock as all legacy research workers.
        # Unlike a TTL lease, this cannot steal ownership from a slow but live process.
        for run in self.store.runs():
            if run['status'] == 'running' and run['name'] in self.definitions:
                self._failed(run, RuntimeError('Worker interrupted'))

    @staticmethod
    def _log(run):
        logger.info(json.dumps({key: run.get(key) for key in
                               ('id', 'name', 'scheduled_at', 'started_at', 'finished_at', 'status',
                                'attempt', 'inputs', 'outputs', 'error')}))

    def _execute(self, identity):
        run = self.store.get(identity)
        if run['status'] not in {'queued', 'retry'} or utc_timestamp(run['next_attempt_at']) > self.now():
            return run
        # The worker lock protects execution; SQLite makes the claim visible atomically.
        run = self.store.update(identity, status='running', phase='preparing',
                                attempt=run['attempt'] + 1, started_at=self.now().isoformat(),
                                finished_at=None, error=None)
        self.store.attempt(run)
        self.store.beat(self.now().isoformat(), state='running', run_id=identity)
        self._log(run)
        context = ExecutionContext(self.store, identity, self.clock)
        try:
            outputs = self.handlers[run['kind']](self.definitions[run['name']], context)
            run = self.store.update(identity, status='succeeded', outputs=outputs,
                                    finished_at=self.now().isoformat())
            self.store.attempt(run)
            self._log(run)
            return run
        except Exception as error:
            return self._failed(self.store.get(identity), error)

    def run_due(self, *, name=None, scheduled_at=None):
        if name is not None and name not in self.definitions:
            raise ValueError('Unknown registered job')
        if scheduled_at is not None:
            if name is None:
                raise ValueError('scheduled-at requires a registered job name')
            stamp = utc_timestamp(scheduled_at)
            if stamp > self.now() or self.definitions[name].latest(stamp) != stamp:
                raise ValueError('scheduled-at must be a due scheduled slot (close plus 30 minutes)')
        with self.runtime.worker_lease() as owned:
            if not owned:
                return {'status': 'busy', 'reason': 'Another research worker owns the lock'}
            self.store.beat(self.now().isoformat(), state='polling')
            self.recover()
            if name is None:
                self.schedule()
                selected_id = None
            else:
                definition = self.definitions[name]
                selected_id = self.enqueue(definition, scheduled_at or definition.latest(self.now()))['id']
            results = []
            order = {'refresh': 0, 'forecast': 1, 'experiment': 2}
            for run in sorted(self.store.runs(), key=lambda r: (order.get(r['kind'], 9), r['scheduled_at'], r['name'])):
                if run['name'] not in self.definitions or (selected_id is not None and run['id'] != selected_id):
                    continue
                if run['status'] in {'queued', 'retry'} and utc_timestamp(run['next_attempt_at']) <= self.now():
                    blocked = any(prior['name'] == run['name'] and prior['status'] == 'blocked'
                                  for prior in self.store.runs())
                    if blocked:
                        results.append(self.store.update(run['id'], status='blocked',
                                       finished_at=self.now().isoformat(),
                                       reason='Earlier ambiguous run requires reconciliation before more effects'))
                    else:
                        results.append(self._execute(run['id']))
            self.store.beat(self.now().isoformat(), state='idle')
            return {'status': 'completed', 'runs': results}

    def reconcile(self, identity, *, outcome, note):
        """Record a human's artifact inspection; never re-execute an ambiguous run."""
        if outcome not in {'succeeded', 'cancelled'} or not note.strip():
            raise ValueError('Reconciliation requires succeeded/cancelled and an inspection note')
        with self.runtime.worker_lease() as owned:
            if not owned:
                return {'status': 'busy', 'reason': 'A research worker is active'}
            run = self.store.get(identity)
            if run['status'] not in {'blocked', 'failed'}:
                raise ValueError('Only blocked or failed runs can be reconciled')
            legacy_id = run['outputs'].get('legacy_job_id')
            if legacy_id and self.runtime.store.get('jobs', legacy_id)['state'] in {'queued', 'running', 'cancelling'}:
                raise ValueError('Finish or cancel the referenced legacy job before reconciliation')
            result = self.store.update(identity, status=outcome,
                                       reconciliation={'at': self.now().isoformat(), 'note': note,
                                                       'previous_status': run['status']})
            self._log(result)
            return result
