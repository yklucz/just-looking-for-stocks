"""Ledger-only administration, without importing or activating model bindings."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .forecast_audit import audit_forecasts
from .forecast_store import get, revisions
from .store import ResearchStore


def database_path():
    root = Path(os.environ.get('STOCK_RESEARCH_ROOT', Path(__file__).resolve().parents[2] / 'artifacts' / 'research'))
    return root / 'research.sqlite3'


def run(args):
    path = Path(args.database or database_path()).resolve()
    if args.command == 'forecast-audit':
        return audit_forecasts(path)
    if args.command == 'forecast-replay':
        from types import SimpleNamespace
        from .data import DataRepository
        from .forecast_replay import replay_revision
        runtime = SimpleNamespace(root=path.parent, store=ResearchStore(path),
                                  data=DataRepository(path.parent / 'datasets'))
        return replay_revision(runtime, args.issuance_id, args.revision_id)
    if args.command == 'migrate-forecasts':
        from .forecast_migration import migrate_forecasts
        # Reuse the operational lock and take a SQLite-consistent backup before any schema write.
        with (path.parent / 'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy'}
            backup = path.with_name(f'{path.stem}.before-canonical-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.sqlite3')
            with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as source, sqlite3.connect(backup) as target:
                source.backup(target)
            store = ResearchStore(path)
            statistics = migrate_forecasts(store)
            audit = audit_forecasts(path)
            return {'status': audit['status'], 'backup': str(backup), 'migration': statistics, 'audit': audit}
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        if args.command == 'forecast-revisions':
            get(db, args.issuance_id)
            return {'items': revisions(db, args.issuance_id)}
        return {'items': [json.loads(row[0]) for row in db.execute(
            "SELECT document FROM records WHERE kind='forecasts' ORDER BY created_at,id")]}
