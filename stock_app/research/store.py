"""Transactional local metadata. Artifacts stay immutable outside SQLite."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4


KINDS = {'datasets', 'jobs', 'models', 'forecasts', 'events', 'settings'}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class ResearchStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise ValueError('Research database is newer than this application')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS records (
                    kind TEXT NOT NULL, id TEXT NOT NULL, symbol TEXT,
                    model_id TEXT, state TEXT, created_at TEXT NOT NULL,
                    document TEXT NOT NULL, PRIMARY KEY(kind,id));
                CREATE INDEX IF NOT EXISTS records_filter ON records(kind,symbol,model_id,state);
                CREATE TABLE IF NOT EXISTS job_keys (key TEXT PRIMARY KEY, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS active_models (
                    symbol TEXT, task TEXT, model_id TEXT NOT NULL, PRIMARY KEY(symbol,task));
                PRAGMA user_version=1;
            ''')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _put(db, kind, value):
        if kind not in KINDS:
            raise ValueError('Unknown record kind')
        record = dict(value)
        record.setdefault('id', uuid4().hex)
        record.setdefault('created_at', utcnow())
        db.execute('''INSERT INTO records VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(kind,id) DO UPDATE SET symbol=excluded.symbol,
            model_id=excluded.model_id,state=excluded.state,document=excluded.document''',
            (kind, record['id'], record.get('symbol'), record.get('model_id'),
             record.get('state', record.get('status')), record['created_at'],
             json.dumps(record, allow_nan=False)))
        return record

    def put(self, kind, value):
        with self.connection() as db:
            return self._put(db, kind, value)

    def get(self, kind, identity):
        with self.connection() as db:
            return self._get(db, kind, identity)

    @staticmethod
    def _get(db, kind, identity):
        row = db.execute('SELECT document FROM records WHERE kind=? AND id=?', (kind, identity)).fetchone()
        if row is None:
            raise KeyError(identity)
        return json.loads(row[0])

    def list(self, kind, *, symbol=None, model_id=None, state=None):
        query, args = 'SELECT document FROM records WHERE kind=?', [kind]
        for column, value in [('symbol', symbol), ('model_id', model_id), ('state', state)]:
            if value:
                query += f' AND {column}=?'
                args.append(value)
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(query + ' ORDER BY created_at DESC,id', args)]

    def update(self, kind, identity, **changes):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            value = self._get(db, kind, identity)
            value.update(changes)
            return self._put(db, kind, value)

    def enqueue(self, kind, symbol, parameters=None, *, key=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if key:
                found = db.execute('SELECT job_id FROM job_keys WHERE key=?', (key,)).fetchone()
                if found:
                    return self._get(db, 'jobs', found[0])
            active = db.execute("SELECT document FROM records WHERE kind='jobs' AND symbol=? "
                                "AND state IN ('queued','running','paused','cancelling')", (symbol,)).fetchall()
            for row in active:
                job = json.loads(row[0])
                if job['kind'] == kind and job['parameters'] == (parameters or {}):
                    if key:
                        db.execute('INSERT INTO job_keys VALUES(?,?)', (key, job['id']))
                    return job
            job = self._put(db, 'jobs', {'kind': kind, 'symbol': symbol, 'state': 'queued',
                                         'parameters': parameters or {}, 'progress': {}})
            if key:
                db.execute('INSERT INTO job_keys VALUES(?,?)', (key, job['id']))
            return job

    def recover_jobs(self):
        for job in self.list('jobs', state='running'):
            self.update('jobs', job['id'], state='queued', interrupted=True)
        for job in self.list('jobs', state='cancelling'):
            self.update('jobs', job['id'], state='cancelled')

    def active(self, symbol, task):
        with self.connection() as db:
            row = db.execute('SELECT model_id FROM active_models WHERE symbol=? AND task=?',
                             (symbol, task)).fetchone()
            return self._get(db, 'models', row[0]) if row else None

    def set_active(self, identity, *, validate=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            candidate = self._get(db, 'models', identity)
            if validate:
                validate(candidate)
            row = db.execute('SELECT model_id FROM active_models WHERE symbol=? AND task=?',
                             (candidate['symbol'], candidate['task'])).fetchone()
            if row and row[0] != identity:
                previous = self._get(db, 'models', row[0])
                previous['state'] = 'retired'
                self._put(db, 'models', previous)
                candidate['previous_active'] = row[0]
            candidate.update(state='active', activated_at=utcnow())
            self._put(db, 'models', candidate)
            db.execute('INSERT INTO active_models VALUES(?,?,?) ON CONFLICT(symbol,task) '
                       'DO UPDATE SET model_id=excluded.model_id',
                       (candidate['symbol'], candidate['task'], identity))
            return candidate

    def rollback(self, identity, *, validate=None):
        candidate = self.get('models', identity)
        if candidate['state'] != 'retired' or not candidate.get('activated_at'):
            raise ValueError('Only previously active versions can be restored')
        return self.set_active(identity, validate=validate)

    def backup(self, path):
        destination = Path(path)
        if destination.resolve() == self.path.resolve() or destination.exists():
            raise ValueError('Backup destination must be a new file')
        with self.connection() as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return destination
