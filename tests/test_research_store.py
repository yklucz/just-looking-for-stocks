"""Persistent records must survive restarts and activation must be atomic."""
import sqlite3

import pytest


def test_persistent_records_and_backup(tmp_path):
    from stock_app.research.store import ResearchStore
    store = ResearchStore(tmp_path / 'research.sqlite3')
    store.put('datasets', {'id': 'one', 'symbol': 'AAPL', 'status': 'valid'})
    assert ResearchStore(store.path).get('datasets', 'one')['symbol'] == 'AAPL'
    store.backup(tmp_path / 'backup.sqlite3')
    assert ResearchStore(tmp_path / 'backup.sqlite3').list('datasets')[0]['id'] == 'one'
    with sqlite3.connect(store.path) as connection:
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1


def test_job_deduplication_and_recovery(tmp_path):
    from stock_app.research.store import ResearchStore
    store = ResearchStore(tmp_path / 'r.db')
    a = store.enqueue('experiment', 'AAPL', {'task': 'binary'}, key='monthly-aapl')
    b = store.enqueue('experiment', 'AAPL', {'task': 'binary'}, key='monthly-aapl')
    assert a['id'] == b['id']
    store.update('jobs', a['id'], state='running')
    store.recover_jobs()
    assert store.get('jobs', a['id'])['state'] == 'queued'


def test_activation_failure_preserves_active_model(tmp_path):
    from stock_app.research.store import ResearchStore
    store = ResearchStore(tmp_path / 'r.db')
    for name, state in [('old', 'active'), ('new', 'shadow')]:
        store.put('models', {'id': name, 'symbol': 'AAPL', 'task': 'binary', 'state': state})
    store.set_active('old')
    with pytest.raises(ValueError):
        store.set_active('new', validate=lambda model: (_ for _ in ()).throw(ValueError('bad artifact')))
    assert store.active('AAPL', 'binary')['id'] == 'old'
    store.set_active('new')
    assert store.get('models', 'old')['state'] == 'retired'
    store.rollback('old')
    assert store.active('AAPL', 'binary')['id'] == 'old'


def test_concurrent_requests_share_one_active_job(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from stock_app.research.store import ResearchStore
    store = ResearchStore(tmp_path / 'r.db')
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: store.enqueue('experiment', 'AAPL', {'task':'binary'})['id'], range(12)))
    assert len(set(ids)) == 1


def test_candidate_identity_must_match_registered_symbol(tmp_path):
    import hashlib
    import json
    from stock_app.research.lifecycle import verify_artifact
    path=tmp_path/'candidate.joblib'
    path.write_bytes(b'opaque-local-model')
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix('.metadata.json').write_text(json.dumps({'artifact_sha256':checksum,
        'ticker':'NVDA','task':'binary','artifact_version':1}))
    with pytest.raises(ValueError, match='identity'):
        verify_artifact({'artifact':str(path), 'artifact_sha256':checksum, 'symbol':'AAPL','task':'binary'})


def test_sql_failure_rolls_back_retirement_and_active_pointer(tmp_path):
    from stock_app.research.store import ResearchStore
    store=ResearchStore(tmp_path/'r.db')
    for identity in ['old','new']:
        store.put('models',{'id':identity,'symbol':'AAPL','task':'binary','state':'candidate'})
    store.set_active('old')
    with store.connection() as db:
        db.execute("CREATE TRIGGER activation_failure BEFORE UPDATE ON records "
                   "WHEN NEW.kind='models' AND NEW.id='new' AND NEW.state='active' "
                   "BEGIN SELECT RAISE(ABORT, 'injected activation failure'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):
        store.set_active('new')
    assert store.active('AAPL','binary')['id']=='old'
    assert store.get('models','old')['state']=='active'
    assert store.get('models','new')['state']=='candidate'
