"""Canonical cardinality, atomic recovery, causal authority and retained evidence."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_app.research import forecast_store
from stock_app.research.forecast_audit import audit_forecasts
from stock_app.research.forecast_identity import digest
from stock_app.research.forecast_migration import migrate_forecasts
from stock_app.research.ledger import issue_forecast, resolve_forecasts
from stock_app.research.store import ResearchStore


@pytest.fixture
def store(tmp_path):
    return ResearchStore(tmp_path / 'research.sqlite3')


def issue(store, **changes):
    args = dict(symbol='AAPL', model_id='m', snapshot_id='s1', origin='2026-07-02',
                payload={'probability': .6, 'origin_close': 100., 'training_prior': .5},
                issued_at='2026-07-02T21:00Z')
    args.update(changes)
    return issue_forecast(store, **args)


def revisions(store, identity):
    with store.connection() as db:
        return forecast_store.revisions(db, identity)


def legacy_model(store):
    contract = {'ticker': 'AAPL', 'interval': '1d', 'model_type': 'xgboost',
                'target': {'task': 'binary', 'horizon': 5, 'threshold': .002},
                'feature_names': ['close'], 'feature_config': {}, 'feature_version': 'v1'}
    version = digest(contract)
    model = {'id': 'legacy-' + version[:32], 'symbol': 'AAPL', 'task': 'binary',
             'state': 'shadow', 'nominated_at': '2020-01-01T00:00:00Z', 'comparison_model_id': None,
             'artifact_sha256': 'a' * 64, 'metadata': {'legacy': True, 'contract': contract,
                 'binding': {'fingerprint': version, 'model_sha256': 'a' * 64}}}
    return store.put('models', model)


def old_row(store, model, identity='old1', snapshot='s1', **changes):
    row = {'id': identity, 'symbol': 'AAPL', 'model_id': model['id'], 'snapshot_id': snapshot,
           'origin': '2026-07-02', 'target': '2026-07-10', 'horizon': 5,
           'issued_at': '2026-07-02T21:00:00+00:00', 'created_at': '2026-07-02T21:00:01+00:00',
           'kind': 'prospective', 'state': 'pending', 'revision_of': None,
           'payload': {'probability': .6, 'origin_close': 100., 'training_prior': .5}}
    row.update(changes)
    return store.put('forecasts', row)


def test_creation_and_exact_retry_are_one_issuance_and_revision(store):
    first = issue(store)
    again = issue(store, issued_at='2026-07-08T21:00Z')
    assert first['id'] == again['id']
    assert first['revision_id'] == again['revision_id']
    assert first['id'].startswith('fc-')
    assert first['authoritative_revision_id'] == first['revision_id']
    assert len(store.list('forecasts')) == len(revisions(store, first['id'])) == 1
    assert audit_forecasts(store.path)['status'] == 'healthy'


def test_changed_input_is_child_not_new_logical_forecast(store):
    first = issue(store)
    changed = issue(store, snapshot_id='s2', issued_at='2026-07-07T22:00Z',
                    payload={'probability': .9, 'origin_close': 101., 'training_prior': .5})
    assert changed['id'] == first['id']
    assert changed['revision_id'] != first['revision_id']
    history = revisions(store, first['id'])
    assert len(history) == 2
    assert history[1]['previous_revision_id'] == first['revision_id']
    assert history[1]['kind'] == 'historical_replay'
    assert history[1]['payload']['probability'] == .9
    authoritative = store.get('forecasts', first['id'])
    assert authoritative['payload']['probability'] == .6
    assert authoritative['snapshot_id'] == 's1'
    assert authoritative['kind'] == 'prospective'
    assert len(store.list('forecasts')) == 1


@pytest.mark.parametrize('change', [dict(model_id='m2'), dict(symbol='SPY'), dict(origin='2026-07-06'),
                                   dict(horizon=6), dict(security_id='permanent-AAPL'),
                                   dict(target_definition={'task': 'binary', 'measure': 'adjusted_close_log_return',
                                                           'operator': '>', 'event_threshold': .003})])
def test_economic_identity_change_creates_separate_issuance(store, change):
    assert issue(store)['id'] != issue(store, **change)['id']
    assert len(store.list('forecasts')) == 2


def test_registry_artifact_version_and_frequency_contract(store):
    model = legacy_model(store)
    first = issue(store, model_id=model['id'])
    store.update('models', model['id'], artifact_sha256='b' * 64)
    assert issue(store, model_id=model['id'])['id'] != first['id']
    with pytest.raises(ValueError, match='daily'):
        issue(store, frequency='1h')
    with pytest.raises(ValueError, match='immutable model'):
        issue(store, model_id=model['id'], horizon=6)


def test_same_input_conflicting_output_is_rejected_without_overwriting(store):
    first = issue(store)
    with pytest.raises(ValueError, match='different outputs'):
        issue(store, payload={'probability': .7, 'origin_close': 100., 'training_prior': .5})
    assert len(revisions(store, first['id'])) == 1
    assert store.get('forecasts', first['id'])['payload']['probability'] == .6


def test_equivalent_input_with_aged_health_does_not_duplicate(store):
    first = issue(store)
    second = issue(store, payload={'probability': .6, 'origin_close': 100., 'training_prior': .5,
                                  'data_freshness': {'AAPL': {'stale': True}}})
    assert second['revision_id'] == first['revision_id']


@pytest.mark.parametrize('snapshot_count', [1, 2])
def test_concurrent_duplicate_issuance_and_revision_attempts(store, snapshot_count):
    def worker(number):
        return issue(store, snapshot_id=f's{number % snapshot_count}')
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(worker, range(16)))
    assert len({r['id'] for r in results}) == 1
    assert len({r['revision_id'] for r in results}) == snapshot_count
    audit = audit_forecasts(store.path)
    assert (audit['issuances'], audit['revisions']) == (1, snapshot_count)
    assert audit['status'] == 'healthy'


@pytest.mark.parametrize('table', ['forecast_issuances', 'forecast_revisions'])
def test_partial_write_rolls_back_and_retry_completes(store, table):
    with store.connection() as db:
        db.execute(f"CREATE TRIGGER injected_failure AFTER INSERT ON {table} BEGIN SELECT RAISE(FAIL,'injected crash'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected crash'):
        issue(store)
    audit = audit_forecasts(store.path)
    assert audit['issuances'] == audit['revisions'] == 0
    with store.connection() as db:
        db.execute('DROP TRIGGER injected_failure')
    assert issue(store)['id'] == issue(store)['id']
    assert audit_forecasts(store.path)['status'] == 'healthy'


def test_failed_later_revision_preserves_original_authority(store):
    first = issue(store)
    with store.connection() as db:
        db.execute("CREATE TRIGGER injected_failure AFTER INSERT ON forecast_revisions BEGIN SELECT RAISE(FAIL,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        issue(store, snapshot_id='s2')
    assert len(revisions(store, first['id'])) == 1
    assert store.get('forecasts', first['id'])['authoritative_revision_id'] == first['revision_id']
    with store.connection() as db:
        db.execute('DROP TRIGGER injected_failure')
    assert issue(store, snapshot_id='s2')['id'] == first['id']
    assert len(revisions(store, first['id'])) == 2


def test_database_immutability_and_revision_ownership_constraints(store):
    first = issue(store)
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with store.connection() as db:
            db.execute('UPDATE forecast_revisions SET output_hash=?', ('wrong',))
    with pytest.raises(sqlite3.IntegrityError, match='immutable'):
        with store.connection() as db:
            db.execute('UPDATE forecast_issuances SET authoritative_revision_id=?', ('other',))
    with pytest.raises(sqlite3.IntegrityError, match='FOREIGN KEY'):
        with store.connection() as db:
            db.execute('INSERT INTO forecast_revisions VALUES(?,?,?,?,?)', ('orphan', 'missing', 'key', 'hash', '{}'))
    with pytest.raises(ValueError, match='immutable'):
        store.update('forecasts', first['id'], payload={'probability': 1.})
    assert audit_forecasts(store.path)['status'] == 'healthy'


def test_migration_preserves_originals_and_is_restart_safe(store):
    model = legacy_model(store)
    first = old_row(store, model)
    later = old_row(store, model, 'old2', 's2', created_at='2026-07-07T21:00:00Z',
                    issued_at='2026-07-07T21:00:00Z', kind='historical_replay', revision_of=first['id'])
    stats = migrate_forecasts(store)
    assert stats == {'historical_rows_examined': 2, 'canonical_issuances_produced': 1,
                     'duplicate_logical_groups_collapsed': 1, 'revision_records_produced': 2,
                     'ambiguous_groups': 0, 'ambiguous_rows': 0, 'unchanged_records': 0}
    assert store.get('forecasts', first['id']) == first
    assert store.get('forecasts', later['id']) == later
    canonical = store.list('forecasts')
    assert len(canonical) == 1
    assert revisions(store, canonical[0]['id'])[1]['provenance']['legacy_record'] == later
    rerun = migrate_forecasts(ResearchStore(store.path))
    assert rerun['unchanged_records'] == 2
    assert rerun['canonical_issuances_produced'] == rerun['revision_records_produced'] == 0
    assert issue(store, model_id=model['id'])['id'] == canonical[0]['id']
    assert len(revisions(store, canonical[0]['id'])) == 2


@pytest.mark.parametrize('ambiguity', ['missing_model', 'missing_horizon', 'conflicting_input',
                                     'invalid_timing', 'tied_authority', 'wrong_target'])
def test_ambiguous_groups_are_retained_and_excluded(store, ambiguity):
    model = legacy_model(store)
    first = old_row(store, model)
    second = old_row(store, model, 'old2', 's2', created_at='2026-07-02T22:00:00Z')
    if ambiguity == 'missing_model':
        with store.connection() as db:
            db.execute("DELETE FROM records WHERE kind='models'")
    elif ambiguity == 'missing_horizon':
        store.update('forecasts', first['id'], horizon=None)
    elif ambiguity == 'conflicting_input':
        store.update('forecasts', second['id'], snapshot_id='s1', payload={'probability': .9, 'origin_close': 100.})
    elif ambiguity == 'invalid_timing':
        store.update('forecasts', first['id'], issued_at='2026-07-10T22:00Z')
    elif ambiguity == 'tied_authority':
        store.update('forecasts', second['id'], created_at=first['created_at'], payload={'probability': .9, 'origin_close': 100.})
    else:
        store.update('forecasts', first['id'], target='2026-07-09')
    stats = migrate_forecasts(store)
    assert stats['ambiguous_groups'] >= 1
    assert audit_forecasts(store.path)['status'] == 'reconciliation_required'
    with store.connection() as db:
        assert db.execute("SELECT count(*) FROM records WHERE kind='forecasts'").fetchone()[0] == 2
    if ambiguity != 'missing_horizon':
        assert store.list('forecasts') == []
    with pytest.raises(ValueError, match='reconciliation'):
        issue(store, model_id=model['id'])


def test_migration_transaction_crash_can_restart(store):
    model = legacy_model(store)
    old_row(store, model)
    with store.connection() as db:
        db.execute("CREATE TRIGGER injected_failure AFTER INSERT ON forecast_migration BEGIN SELECT RAISE(FAIL,'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        migrate_forecasts(store)
    assert audit_forecasts(store.path)['issuances'] == 0
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM forecast_migration').fetchone()[0] == 0
        db.execute('DROP TRIGGER injected_failure')
    assert migrate_forecasts(store)['canonical_issuances_produced'] == 1


def test_evaluation_counts_once_and_late_revision_cannot_improve_it(store):
    from stock_app.research.lifecycle import qualification
    store.put('models', {'id': 'm', 'symbol': 'AAPL', 'task': 'binary', 'state': 'shadow',
                         'nominated_at': '2020-01-01T00:00:00Z', 'comparison_model_id': None})
    first = issue(store)
    dates = pd.to_datetime(['2026-07-02', '2026-07-06', '2026-07-07', '2026-07-08', '2026-07-09', '2026-07-10'], utc=True)
    history = pd.DataFrame({'Close': [100., 101., 102., 103., 104., 110.]}, index=dates)
    resolve_forecasts(store, 'AAPL', history, snapshot_id='outcome', now='2026-07-10T22:00Z')
    before = store.get('forecasts', first['id'])
    assert qualification(store, 'm')['resolved'] == 1
    for number in range(5):
        issue(store, snapshot_id=f'late-{number}', issued_at='2026-07-12T22:00Z',
              payload={'probability': 1., 'origin_close': 90., 'training_prior': .5})
    after = store.get('forecasts', first['id'])
    assert before == after
    assert qualification(store, 'm')['resolved'] == 1
    assert after['outcome']['price_on_issue_basis'] == pytest.approx(110.)


def test_replay_first_never_becomes_prospective_by_later_revision(store):
    first = issue(store, force_replay=True)
    issue(store, snapshot_id='s2')
    assert store.get('forecasts', first['id'])['kind'] == 'historical_replay'


def test_api_defaults_canonical_and_explicit_history_is_available(store, monkeypatch):
    from stock_app.app import app
    from stock_app.research import api
    runtime = SimpleNamespace(store=store)
    monkeypatch.setattr(api, 'runtime', lambda: runtime)
    monkeypatch.setattr(api, 'get_runtime', lambda: runtime)
    first = issue(store)
    issue(store, snapshot_id='s2')
    app.config.update(TESTING=True)
    client = app.test_client()
    rows = client.get('/api/research/forecasts').json['items']
    assert len(rows) == 1
    assert rows[0]['id'] == first['id']
    response = client.get(f"/api/research/forecasts/{first['id']}/revisions")
    assert len(response.json['items']) == 2
    assert client.get('/api/research/forecasts/missing/revisions').status_code == 404
    assert client.get('/api/research/forecasts?format=csv').status_code == 200


def test_audit_read_only_and_detects_metadata_tampering(store):
    first = issue(store)
    with store.connection() as db:
        before = db.execute('SELECT total_changes()').fetchone()[0]
    assert audit_forecasts(store.path)['status'] == 'healthy'
    with store.connection() as db:
        assert db.execute('SELECT total_changes()').fetchone()[0] == before
        value = json.loads(db.execute('SELECT document FROM forecast_issuances').fetchone()[0])
        value['identity']['horizon'] = 999
        db.execute('UPDATE forecast_issuances SET document=? WHERE id=?', (json.dumps(value), first['id']))
    assert audit_forecasts(store.path)['status'] == 'invalid'


def test_unmigrated_origin_blocks_new_write(store):
    model = legacy_model(store)
    old_row(store, model)
    with pytest.raises(ValueError, match='migration'):
        issue(store, model_id=model['id'])
    assert audit_forecasts(store.path)['status'] == 'reconciliation_required'


def test_full_prospective_metrics_do_not_improve_from_perfect_late_revisions(store):
    from stock_app.research.calendar import sessions_between, session_close
    from stock_app.research.lifecycle import qualification
    store.put('models', {'id': 'm', 'symbol': 'AAPL', 'task': 'binary', 'state': 'shadow',
                         'nominated_at': '2020-01-01T00:00:00Z', 'comparison_model_id': None})
    days = sessions_between('2025-01-01', '2025-12-31')[:126]
    for number, day in enumerate(days):
        row = issue(store, origin=day, issued_at=session_close(day) + pd.Timedelta(minutes=1),
                    payload={'probability': .5, 'training_prior': .5, 'origin_close': 100.})
        store.update('forecasts', row['id'], state='resolved',
                     outcome={'event': number % 2, 'log_return': .02 if number % 2 else -.02})
    before = qualification(store, 'm')
    for number, day in enumerate(days):
        issue(store, origin=day, snapshot_id='late', issued_at='2026-07-12T22:00Z',
              payload={'probability': float(number % 2), 'training_prior': .5, 'origin_close': 100.})
    after = qualification(store, 'm')
    assert before == after
    assert after['resolved'] == 126
    assert after['metrics']['loss'] == .25
    assert after['eligible'] is False
    audit = audit_forecasts(store.path)
    assert (audit['issuances'], audit['revisions']) == (126, 252)


def raw_history():
    return pd.DataFrame({'Open': [100.], 'High': [103.], 'Low': [99.], 'Close': [102.],
                         'Adj Close': [102.], 'Volume': [100.], 'Dividends': [0.], 'Stock Splits': [0.]},
                         index=pd.DatetimeIndex(['2026-07-02']))


def test_operational_reexecution_reuses_canonical_ids_and_verified_cache(tmp_path, monkeypatch):
    from stock_app.jobs import JobService
    from stock_app.jobs.core import ExecutionContext
    from stock_app.research import inference, lifecycle
    clock = lambda: pd.Timestamp('2026-07-02T22:00Z')
    service = JobService(tmp_path, clock=clock)
    for symbol in ('AAPL', 'SPY', 'QQQ', 'XLK'):
        service.runtime.data.ingest(symbol, raw_history(), now=clock())
    service.runtime.store.put('models', {'id': 'm', 'symbol': 'AAPL', 'task': 'binary', 'state': 'shadow',
                                        'artifact': 'fixture', 'artifact_sha256': 'a' * 64})
    # Only estimator math/artifact IO are substituted; real inputs, ledger and job paths run.
    monkeypatch.setattr(lifecycle, 'verify_artifact', lambda model: None)
    calls = []

    def predict(model, history, contexts):
        calls.append(model['id'])
        return {'origin_time': history.index[-1].isoformat(), 'probability': .6, 'origin_close': 102.}

    monkeypatch.setattr(inference, 'model_payload', predict)
    result = service.runner.run_due(name='forecast:AAPL')['runs'][0]
    assert result['status'] == 'succeeded'
    context = ExecutionContext(service.runner.store, result['id'], clock)
    # Retry the same deterministic service after an imagined lost job completion acknowledgement.
    again = service.forecast(service.runner.definitions['forecast:AAPL'], context)
    assert again['forecast_ids'] == result['outputs']['forecast_ids']
    assert again['revision_ids'] == result['outputs']['revision_ids']
    assert calls == ['m']
    assert audit_forecasts(service.runtime.store.path)['revisions'] == 1


def test_offline_replay_preserves_values_and_cardinality(tmp_path, monkeypatch):
    from stock_app.research.runtime import ResearchRuntime
    from stock_app.research.forecast_replay import replay_revision
    from stock_app.research import inference
    runtime = ResearchRuntime(tmp_path, import_legacy=False)
    meta = runtime.data.ingest('AAPL', raw_history(), now='2026-07-02T22:00Z')
    runtime.store.put('models', {'id': 'm', 'symbol': 'AAPL', 'task': 'binary', 'state': 'shadow',
                                'artifact_sha256': 'a' * 64})
    row = issue(runtime.store, snapshot_id='mapping', payload={'probability': .6, 'origin_close': 102.,
                                                              'snapshots': {'AAPL': meta['id']}})
    monkeypatch.setattr(inference, 'model_payload', lambda model, history, contexts: {
        'origin_time': str(history.index[-1]), 'probability': .6, 'origin_close': float(history.Close.iloc[-1])})
    before = audit_forecasts(runtime.store.path)
    replay = replay_revision(runtime, row['id'], row['revision_id'])
    assert replay['matches'] is True
    assert replay['recorded'] == replay['replayed']
    assert audit_forecasts(runtime.store.path) == before
    from pathlib import Path
    Path(meta['path']).write_text('corrupt')
    with pytest.raises(ValueError, match='checksum'):
        replay_revision(runtime, row['id'], row['revision_id'])


def test_migration_backup_and_old_database_reads_are_preserved(store):
    from stock_app.research.forecast_admin import run
    model = legacy_model(store)
    original = old_row(store, model)
    result = run(SimpleNamespace(database=store.path, command='migrate-forecasts'))
    assert result['status'] == 'healthy'
    with sqlite3.connect(result['backup']) as backup:
        saved = json.loads(backup.execute("SELECT document FROM records WHERE kind='forecasts'").fetchone()[0])
    assert saved == original
    with sqlite3.connect(store.path) as db:
        # An older reader still has every original record; canonical writes are in separate tables.
        assert json.loads(db.execute("SELECT document FROM records WHERE kind='forecasts'").fetchone()[0]) == original
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1


def test_migration_same_equivalent_revision_preserves_both_original_rows(store):
    model = legacy_model(store)
    old_row(store, model)
    old_row(store, model, identity='duplicate-row', created_at='2026-07-02T22:00Z')
    stats = migrate_forecasts(store)
    assert stats['canonical_issuances_produced'] == stats['revision_records_produced'] == 1
    with store.connection() as db:
        assert db.execute('SELECT count(*) FROM forecast_migration').fetchone()[0] == 2
    assert audit_forecasts(store.path)['historical_rows'] == 2


def test_canonical_list_uses_one_join_without_per_forecast_queries(store):
    for index in range(8):
        issue(store, model_id=f'm-{index}')
        issue(store, model_id=f'm-{index}', snapshot_id='s2')
    queries = []
    with store.connection() as db:
        db.set_trace_callback(queries.append)
        rows = forecast_store.list_issuances(db, symbol='AAPL')
    assert len(rows) == 8
    assert len([q for q in queries if q.lstrip().upper().startswith('SELECT')]) == 1


def test_audit_does_not_create_missing_schema_or_change_existing_bytes(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE records(kind TEXT,id TEXT,document TEXT)')
        db.execute('INSERT INTO records VALUES(?,?,?)', ('forecasts', 'old', '{}'))
    before = path.read_bytes()
    result = audit_forecasts(path)
    assert result['status'] == 'migration_required'
    assert path.read_bytes() == before


def test_audit_reports_ambiguous_reasons(store):
    model = legacy_model(store)
    row = old_row(store, model)
    store.update('forecasts', row['id'], target='wrong')
    migrate_forecasts(store)
    result = audit_forecasts(store.path)
    assert result['reconciliation'][0]['legacy_id'] == row['id']
    assert 'target' in result['reconciliation'][0]['reason']


@pytest.mark.parametrize('snapshot', ['s1', 'new-input'])
def test_later_migration_conflict_with_existing_authority_is_quarantined(store, snapshot):
    model = legacy_model(store)
    original = old_row(store, model)
    migrate_forecasts(store)
    before = store.list('forecasts')[0]
    old_row(store, model, identity='conflicting-row', snapshot=snapshot,
            created_at=original['created_at'], payload={'probability': .9, 'origin_close': 100., 'training_prior': .5})
    result = migrate_forecasts(store)
    assert result['ambiguous_groups'] == result['ambiguous_rows'] == 1
    assert store.list('forecasts')[0] == before
    assert audit_forecasts(store.path)['revisions'] == 1
