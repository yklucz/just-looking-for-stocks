"""Additive operational tables in the existing research database (schema v1).

Kept separate from the historical jobs API so its enqueue coalescing cannot
collapse two distinct logical scheduled runs. Old applications ignore these tables.
"""
import json

from stock_app.research.store import ResearchStore


class JobStore:
    def __init__(self, research: ResearchStore):
        self.research = research
        with research.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS operational_schedules (
                    name TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operational_runs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, scheduled_at TEXT NOT NULL,
                    status TEXT NOT NULL, document TEXT NOT NULL,
                    UNIQUE(name, scheduled_at));
                CREATE INDEX IF NOT EXISTS operational_runs_status
                    ON operational_runs(status, scheduled_at);
                CREATE TABLE IF NOT EXISTS operational_attempts (
                    run_id TEXT NOT NULL, attempt INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(run_id, attempt));
                CREATE TABLE IF NOT EXISTS operational_heartbeat (
                    id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL);
            ''')

    @staticmethod
    def save_run(db, run):
        db.execute('''INSERT INTO operational_runs VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status, document=excluded.document''',
                   (run['id'], run['name'], run['scheduled_at'], run['status'], json.dumps(run)))

    def update(self, identity, **changes):
        with self.research.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM operational_runs WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            run = json.loads(row[0])
            run.update(changes)
            self.save_run(db, run)
            return run

    def runs(self):
        with self.research.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM operational_runs ORDER BY scheduled_at,name')]

    def get(self, identity):
        with self.research.connection() as db:
            row = db.execute('SELECT document FROM operational_runs WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise KeyError(identity)
            return json.loads(row[0])

    def schedules(self):
        with self.research.connection() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT document FROM operational_schedules')]

    def attempt(self, run):
        with self.research.connection() as db:
            db.execute('INSERT OR REPLACE INTO operational_attempts VALUES(?,?,?)',
                       (run['id'], run['attempt'], json.dumps(run)))

    def attempts(self):
        with self.research.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT document FROM operational_attempts ORDER BY run_id,attempt')]

    def beat(self, now, **fields):
        with self.research.connection() as db:
            db.execute('INSERT OR REPLACE INTO operational_heartbeat VALUES(1,?)',
                       (json.dumps({'at': now, **fields}),))

    def heartbeat(self):
        with self.research.connection() as db:
            row = db.execute('SELECT document FROM operational_heartbeat WHERE id=1').fetchone()
            return json.loads(row[0]) if row else None
