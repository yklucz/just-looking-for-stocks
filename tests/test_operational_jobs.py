"""Offline contract tests: real SQLite/filesystem locking, injected clock/provider."""
import json
import subprocess
import sys

import pandas as pd
import pytest

from stock_app.jobs import JobDefinition, JobRunner, JobService
from stock_app.jobs.health import job_health, source_freshness
from stock_app.research.runtime import ResearchRuntime
from stock_app.research.store import ResearchStore


class Clock:
    def __init__(self, at='2024-12-02T22:00Z'):
        self.at = pd.Timestamp(at)

    def __call__(self):
        return self.at

    def advance(self, **kwargs):
        self.at += pd.Timedelta(**kwargs)


@pytest.fixture
def rig(tmp_path):
    clock = Clock()
    runtime = ResearchRuntime(tmp_path / 'research', import_legacy=False)
    calls = []

    def handler(job, context):
        context.begin_effects({'fixture': 'offline'})
        calls.append(context.run_id)
        return {'snapshot_id': 'fixture'}

    definition = JobDefinition('refresh:AAPL', 'refresh', 'AAPL')
    runner = JobRunner(runtime, [definition], {'refresh': handler}, clock=clock)
    return runner, clock, calls


def test_success_persisted_inputs_outputs_attempts_and_duplicate(rig):
    runner, clock, calls = rig
    result = runner.run_due()
    run = result['runs'][0]
    assert run['status'] == 'succeeded'
    assert run['scheduled_at'] == '2024-12-02T21:30:00+00:00'
    assert run['started_at'] == run['finished_at'] == clock().isoformat()
    assert run['attempt'] == 1
    assert run['inputs'] == {'fixture': 'offline'}
    assert run['outputs'] == {'snapshot_id': 'fixture'}
    assert runner.store.attempts()[0]['status'] == 'succeeded'
    assert runner.run_due(name='refresh:AAPL')['runs'] == []
    assert len(calls) == 1
    reopened = JobRunner(runner.runtime, list(runner.definitions.values()), runner.handlers, clock=clock)
    assert reopened.store.get(run['id']) == run
    assert reopened.run_due()['runs'] == []


def test_busy_runner_does_not_claim_or_recover(rig):
    runner, _, calls = rig
    with runner.runtime.worker_lease() as owned:
        assert owned
        assert runner.run_due()['status'] == 'busy'
    assert calls == []
    assert runner.store.runs() == []
    assert runner.store.heartbeat() is None


def test_bounded_retry_and_attempt_history(rig):
    runner, clock, _ = rig
    runner.definitions['refresh:AAPL'] = JobDefinition('refresh:AAPL', 'refresh', 'AAPL',
                                                      max_attempts=4, retry_seconds=60, max_retry_seconds=90)

    def fail(*args):
        raise ConnectionError('secret-token-must-not-appear')

    runner.handlers['refresh'] = fail
    first = runner.run_due()['runs'][0]
    assert first['status'] == 'retry'
    assert pd.Timestamp(first['next_attempt_at']) == clock() + pd.Timedelta(seconds=60)
    assert runner.run_due()['runs'] == []
    clock.advance(seconds=60)
    second = runner.run_due()['runs'][0]
    assert pd.Timestamp(second['next_attempt_at']) == clock() + pd.Timedelta(seconds=90)
    clock.advance(seconds=90)
    assert runner.run_due()['runs'][0]['status'] == 'retry'
    clock.advance(seconds=90)
    assert runner.run_due()['runs'][0]['status'] == 'failed'
    clock.advance(hours=1)
    assert runner.run_due()['runs'] == []
    assert [item['attempt'] for item in runner.store.attempts()] == [1, 2, 3, 4]
    assert 'secret-token' not in json.dumps(runner.store.attempts())


def test_retry_after_preparation_failure_can_succeed(rig):
    runner, clock, calls = rig
    original = runner.handlers['refresh']
    runner.handlers['refresh'] = lambda *args: (_ for _ in ()).throw(ConnectionError())
    assert runner.run_due()['runs'][0]['status'] == 'retry'
    runner.handlers['refresh'] = original
    clock.advance(minutes=1)
    assert runner.run_due()['runs'][0]['status'] == 'succeeded'
    assert len(calls) == 1


def test_partial_effects_block_retry_and_later_slots(rig):
    runner, clock, calls = rig

    def partial(job, context):
        context.begin_effects({'pinned': 'snapshot'})
        calls.append(context.run_id)
        context.store.update(context.run_id, outputs={'snapshot_id': 'partial-output'})
        raise OSError('failure after write')

    runner.handlers['refresh'] = partial
    first = runner.run_due()['runs'][0]
    assert first['status'] == 'blocked'
    assert first['outputs']['snapshot_id'] == 'partial-output'
    clock.advance(days=1)
    assert runner.run_due()['runs'][0]['status'] == 'blocked'
    assert len(calls) == 1
    assert any('blocked' in warning for warning in job_health(runner)['warnings'])


@pytest.mark.parametrize('effects,expected', [(False, 'retry'), (True, 'blocked')])
def test_process_restart_recovery_respects_effect_fence(rig, effects, expected):
    runner, clock, calls = rig

    def crash(job, context):
        if effects:
            context.begin_effects({'pinned': 'snapshot'})
        raise SystemExit('simulate process death')

    runner.handlers['refresh'] = crash
    with pytest.raises(SystemExit):
        runner.run_due()
    assert runner.store.runs()[0]['status'] == 'running'
    runtime = ResearchRuntime(runner.runtime.root, import_legacy=False)
    restarted = JobRunner(runtime, list(runner.definitions.values()),
                          {'refresh': lambda *args: {'recovered': True}}, clock=clock)
    restarted.run_due()
    assert restarted.store.runs()[0]['status'] == expected
    if not effects:
        clock.advance(minutes=1)
        assert restarted.run_due()['runs'][0]['status'] == 'succeeded'
        assert len(restarted.store.attempts()) == 2
    assert calls == []


def test_missed_sessions_coalesce_with_explicit_history(rig):
    runner, clock, calls = rig
    runner.run_due()
    clock.advance(days=7)
    assert any('missed' in warning for warning in job_health(runner)['warnings'])
    runner.run_due()
    runs = runner.store.runs()
    assert [item['status'] for item in runs] == ['succeeded', 'skipped', 'skipped', 'skipped', 'skipped', 'succeeded']
    assert len(calls) == 2
    assert all(item['attempt'] == 0 for item in runs if item['status'] == 'skipped')
    assert runner.run_due()['runs'] == []


def test_pending_old_slot_is_superseded(rig):
    runner, clock, calls = rig
    runner.schedule()
    clock.advance(days=1)
    runner.run_due()
    assert [run['status'] for run in runner.store.runs()] == ['skipped', 'succeeded']
    assert len(calls) == 1


def test_holiday_early_close_and_monthly_catchup():
    daily = JobDefinition('daily', 'refresh', 'AAPL')
    assert daily.latest('2026-11-27T18:15Z') == pd.Timestamp('2026-11-25T21:30Z')
    assert daily.latest('2026-11-27T18:35Z') == pd.Timestamp('2026-11-27T18:30Z')
    assert daily.latest('2026-11-28T22:00Z') == pd.Timestamp('2026-11-27T18:30Z')
    monthly = JobDefinition('monthly', 'experiment', 'AAPL', cadence='monthly')
    slots = monthly.slots('2026-09-01T20:30Z', '2026-12-02T22:00Z')
    assert [slot.date().isoformat() for slot in slots] == ['2026-10-01', '2026-11-02', '2026-12-01']


def test_manual_run_uses_stable_slot_and_rejects_arbitrary_times(rig):
    runner, _, calls = rig
    runner.run_due(name='refresh:AAPL', scheduled_at='2024-12-02T21:30Z')
    runner.run_due(name='refresh:AAPL', scheduled_at='2024-12-02T16:30-05:00')
    assert len(calls) == 1
    with pytest.raises(ValueError):
        runner.run_due(name='unknown')
    with pytest.raises(ValueError):
        runner.run_due(name='refresh:AAPL', scheduled_at='2024-12-02T21:31Z')
    with pytest.raises(ValueError):
        runner.run_due(name='refresh:AAPL', scheduled_at='2024-12-03T21:30Z')


def history():
    index = pd.DatetimeIndex(['2024-11-27', '2024-11-29', '2024-12-02'])
    return pd.DataFrame({'Open': [100., 101., 102.], 'High': [103., 104., 105.],
                         'Low': [99., 100., 101.], 'Close': [102., 103., 104.],
                         'Adj Close': [51., 51.5, 52.], 'Volume': [100, 200, 300],
                         'Dividends': [0., 0., 0.], 'Stock Splits': [0., 0., 0.]}, index=index)


@pytest.mark.parametrize('at,status', [('2024-12-02T22:00Z', 'fresh'),
                                      ('2024-12-03T22:00Z', 'warning'),
                                      ('2024-12-04T22:00Z', 'stale')])
def test_freshness_from_persisted_sessions(rig, at, status):
    runner, _, _ = rig
    meta = runner.runtime.data.ingest('AAPL', history(), now='2024-12-02T22:00Z')
    before = runner.runtime.data.list_snapshots('AAPL')
    result = source_freshness(runner.runtime.data, 'AAPL', at)
    assert result['status'] == status
    assert result['snapshot_id'] == meta['id']
    assert runner.runtime.data.list_snapshots('AAPL') == before


def test_freshness_unknown_missing_corrupt_future_metadata(rig, monkeypatch):
    runner, clock, _ = rig
    assert source_freshness(runner.runtime.data, 'AAPL', clock())['status'] == 'unknown'
    meta = runner.runtime.data.ingest('AAPL', history(), now=clock())
    from pathlib import Path
    Path(meta['path']).write_text('corrupt')
    assert source_freshness(runner.runtime.data, 'AAPL', clock())['status'] == 'unknown'
    monkeypatch.setattr(runner.runtime.data, 'latest', lambda *args, **kwargs: {'status': 'valid'})
    assert source_freshness(runner.runtime.data, 'AAPL', clock())['status'] == 'unknown'
    monkeypatch.setattr(runner.runtime.data, 'latest', lambda *args, **kwargs: {
        'status': 'valid', 'created_at': '2025-01-01T00:00Z', 'last_session': '2024-12-02'})
    assert source_freshness(runner.runtime.data, 'AAPL', clock())['status'] == 'unknown'


def test_health_heartbeat_source_and_lock(rig):
    runner, clock, _ = rig
    assert job_health(runner)['status'] == 'warning'
    runner.runtime.data.ingest('AAPL', history(), now=clock())
    runner.run_due()
    assert job_health(runner)['status'] == 'healthy'
    clock.advance(minutes=31)
    health = job_health(runner)
    assert health['status'] == 'warning'
    assert 'heartbeat is overdue' in ' '.join(health['warnings'])
    with runner.runtime.worker_lease():
        assert job_health(runner)['worker_active']


def test_additive_schema_preserves_legacy_history_and_backup(tmp_path):
    store = ResearchStore(tmp_path / 'research.sqlite3')
    original = store.put('datasets', {'id': 'historical', 'symbol': 'AAPL', 'sha256': 'original'})
    legacy = store.enqueue('refresh', 'AAPL', key='legacy-key')
    service = JobService(tmp_path)
    assert service.runtime.store.get('datasets', 'historical') == original
    assert service.runtime.store.get('jobs', legacy['id']) == legacy
    service.runner.enqueue(service.runner.definitions['refresh:AAPL'], '2024-12-02T21:30Z')
    backup = store.backup(tmp_path / 'backup.sqlite3')
    from stock_app.jobs.store import JobStore
    assert len(JobStore(ResearchStore(backup)).runs()) == 1
    with store.connection() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    assert JobService(tmp_path).runtime.store.get('datasets', 'historical') == original


def test_cli_does_not_activate_legacy_models_or_import_flask(tmp_path, monkeypatch):
    manifest = tmp_path / 'models.json'
    manifest.write_text(json.dumps({'bindings': {'AAPL:xgboost': {
        'fingerprint': 'a' * 64, 'artifact': 'not-needed'}}}))
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(manifest))
    monkeypatch.setenv('STOCK_RESEARCH_ROOT', str(tmp_path / 'research'))
    result = subprocess.run([sys.executable, '-m', 'stock_app.jobs', 'status'], capture_output=True, text=True, check=True)
    assert 'refresh:AAPL' in json.loads(result.stdout)['registered_jobs']
    store = ResearchStore(tmp_path / 'research' / 'research.sqlite3')
    assert store.list('models') == []
    script = 'from stock_app.jobs import JobService; import sys; assert "flask" not in sys.modules'
    subprocess.run([sys.executable, '-c', script], check=True)
    health = subprocess.run([sys.executable, '-m', 'stock_app.jobs', 'health'], capture_output=True, text=True)
    assert health.returncode == 1
    assert json.loads(health.stdout)['sources'][0]['status'] == 'unknown'


def test_real_refresh_adapter_idempotent_and_provider_failure_retry(tmp_path):
    clock = Clock()
    calls = []

    def download(symbol):
        calls.append(symbol)
        if len(calls) == 1:
            raise ConnectionError('offline')
        return history()

    service = JobService(tmp_path, clock=clock, downloader=download)
    first = service.runner.run_due(name='refresh:AAPL')['runs'][0]
    assert first['status'] == 'retry'
    assert service.runtime.data.list_snapshots() == []
    clock.advance(minutes=1)
    run = service.runner.run_due(name='refresh:AAPL')['runs'][0]
    assert run['status'] == 'succeeded'
    assert run['outputs']['freshness']['status'] == 'fresh'
    assert service.runner.run_due(name='refresh:AAPL')['runs'] == []
    assert len(service.runtime.data.list_snapshots()) == 1
    assert len(service.runtime.store.list('datasets')) == 1
    assert calls == ['AAPL', 'AAPL']


def test_downstream_stale_guard_precedes_any_effect(tmp_path, monkeypatch):
    service = JobService(tmp_path, clock=Clock(), downloader=lambda symbol: history())
    monkeypatch.setattr(service.runtime, 'issue_daily', lambda *args, **kwargs: pytest.fail('must not issue'))
    run = service.runner.run_due(name='forecast:AAPL')['runs'][0]
    assert run['status'] == 'retry'
    assert run['phase'] == 'preparing'
    assert service.runtime.store.list('forecasts') == []


def test_forecast_pins_inputs_and_partial_failure_blocks(tmp_path, monkeypatch):
    clock = Clock()
    service = JobService(tmp_path, clock=clock)
    mapping = {symbol: service.runtime.data.ingest(symbol, history(), now=clock())['id']
               for symbol in ('AAPL', 'SPY', 'QQQ', 'XLK')}
    calls = []

    def issue(symbol, *, snapshots):
        assert snapshots == mapping
        calls.append(symbol)
        return {'issued': ['already-written'], 'errors': [{'error': 'partial'}]}

    monkeypatch.setattr(service.runtime, 'issue_daily', issue)
    run = service.runner.run_due(name='forecast:AAPL')['runs'][0]
    assert run['status'] == 'blocked'
    assert run['inputs']['snapshots'] == mapping
    assert run['outputs']['forecast_ids'] == ['already-written']
    service.runner.run_due(name='forecast:AAPL')
    assert calls == ['AAPL']


def test_experiment_uses_legacy_service_with_pinned_inputs_and_no_activation(tmp_path, monkeypatch):
    from stock_app.research import experiments
    clock = Clock()
    service = JobService(tmp_path, clock=clock)
    for symbol in ('AAPL', 'SPY', 'QQQ', 'XLK'):
        service.runtime.data.ingest(symbol, history(), now=clock())
    candidate = tmp_path / 'candidate.joblib'
    candidate.write_bytes(b'immutable candidate')
    monkeypatch.setattr(experiments, 'run_experiment', lambda *args, **kwargs: {
        'status': 'completed', 'candidate': {'path': str(candidate)}})
    run = service.runner.run_due(name='experiment:AAPL:binary')['runs'][0]
    assert run['status'] == 'succeeded'
    assert run['outputs']['job_id'].startswith('operational-')
    assert service.runtime.store.active('AAPL', 'binary') is None
    assert service.runtime.store.list('models')[0]['state'] == 'candidate'
    assert len(service.runtime.store.list('jobs')) == 1
    service.runner.run_due(name='experiment:AAPL:binary')
    assert len(service.runtime.store.list('jobs')) == 1


def test_legacy_recovery_does_not_bypass_operational_fence(tmp_path):
    store = ResearchStore(tmp_path / 'research.sqlite3')
    store.put('jobs', {'id': 'operational-crashed', 'state': 'running'})
    store.put('jobs', {'id': 'legacy-crashed', 'state': 'running'})
    store.recover_jobs()
    assert store.get('jobs', 'operational-crashed')['state'] == 'running'
    assert store.get('jobs', 'legacy-crashed')['state'] == 'queued'


@pytest.mark.parametrize('legacy_state,expected', [('completed', 'succeeded'), ('running', 'blocked'),
                                                  ('queued', 'blocked'), ('failed', 'blocked')])
def test_adopts_legacy_scheduled_identity_without_repeating_effects(rig, legacy_state, expected):
    runner, _, calls = rig
    legacy = runner.runtime.submit('refresh', 'AAPL', key='refresh:AAPL:2024-12-02')
    runner.runtime.store.update('jobs', legacy['id'], state=legacy_state)
    runner.run_due()
    run = runner.store.runs()[0]
    assert run['status'] == expected
    assert run['outputs']['legacy_job_id'] == legacy['id']
    assert calls == []


def test_freshness_grace_period_does_not_warn_before_scheduled_close(rig):
    runner, _, _ = rig
    runner.runtime.data.ingest('AAPL', history().iloc[:2], now='2024-12-02T12:00Z')
    assert source_freshness(runner.runtime.data, 'AAPL', '2024-12-02T21:15Z')['status'] == 'fresh'
    assert source_freshness(runner.runtime.data, 'AAPL', '2024-12-02T21:30Z')['status'] == 'warning'


def test_lock_excludes_separate_cli_process(rig, monkeypatch):
    runner, _, _ = rig
    monkeypatch.setenv('STOCK_RESEARCH_ROOT', str(runner.runtime.root))
    with runner.runtime.worker_lease():
        result = subprocess.run([sys.executable, '-m', 'stock_app.jobs', 'run-due'],
                                capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'busy'
    assert runner.store.runs() == []


def test_reconciliation_retains_attempt_evidence_and_never_reexecutes(rig):
    runner, clock, calls = rig

    def fail(job, context):
        context.begin_effects({'fixture': True})
        raise OSError()

    runner.handlers['refresh'] = fail
    run = runner.run_due()['runs'][0]
    with pytest.raises(ValueError):
        runner.reconcile(run['id'], outcome='cancelled', note='')
    result = runner.reconcile(run['id'], outcome='cancelled', note='Inspected partial artifacts; retained as evidence')
    assert result['status'] == 'cancelled'
    assert result['reconciliation']['previous_status'] == 'blocked'
    assert runner.store.attempts()[0]['status'] == 'blocked'
    assert runner.run_due(name='refresh:AAPL')['runs'] == []
    clock.advance(days=1)
    runner.handlers['refresh'] = lambda *args: {'new_slot': True}
    assert runner.run_due()['runs'][0]['status'] == 'succeeded'
    assert calls == []


def test_stale_provider_response_retries_before_snapshot_write(tmp_path):
    service = JobService(tmp_path, clock=Clock(), downloader=lambda symbol: history().iloc[:2])
    run = service.runner.run_due(name='refresh:AAPL')['runs'][0]
    assert run['status'] == 'retry'
    assert 'latest required completed session' in run['error']['reason']
    assert service.runtime.data.list_snapshots() == []


def test_adopted_legacy_job_must_finish_before_reconciliation(rig):
    runner, _, _ = rig
    legacy = runner.runtime.submit('refresh', 'AAPL', key='refresh:AAPL:2024-12-02')
    runner.run_due()
    run = runner.store.runs()[0]
    with pytest.raises(ValueError, match='Finish or cancel'):
        runner.reconcile(run['id'], outcome='cancelled', note='Not yet inspected')
    runner.runtime.control(legacy['id'], 'cancel')
    assert runner.reconcile(run['id'], outcome='cancelled', note='Legacy job cancelled before effects')['status'] == 'cancelled'


def test_snapshot_write_failure_retains_artifact_and_blocks_duplicate(tmp_path, monkeypatch):
    service = JobService(tmp_path, clock=Clock(), downloader=lambda symbol: history())
    original_put = service.runtime.store.put

    def fail_registration(kind, value):
        if kind == 'datasets':
            raise OSError('simulated database registration failure')
        return original_put(kind, value)

    monkeypatch.setattr(service.runtime.store, 'put', fail_registration)
    run = service.runner.run_due(name='refresh:AAPL')['runs'][0]
    assert run['status'] == 'blocked'
    snapshot = service.runtime.data.list_snapshots()[0]
    assert run['outputs']['snapshot_id'] == snapshot['id']
    service.runtime.data.load(snapshot['id'])  # Intact even when registration failed.
    service.runner.run_due(name='refresh:AAPL')
    assert len(service.runtime.data.list_snapshots()) == 1


def test_flask_manual_worker_never_schedules_or_executes_operational_jobs(rig, monkeypatch):
    runner, _, _ = rig
    runtime = runner.runtime
    runtime.store.put('jobs', {'id': 'operational-owned', 'state': 'queued', 'kind': 'experiment'})
    monkeypatch.setattr(runtime, 'schedule', lambda: pytest.fail('Flask must not schedule'))
    monkeypatch.setattr(runtime, 'run_job', lambda identity: pytest.fail('Operational job must not be executed'))

    class StopAfterIteration:
        def wait(self, seconds):
            raise SystemExit()

    runtime._wake = StopAfterIteration()
    with pytest.raises(SystemExit):
        runtime._loop()


def test_single_run_does_not_execute_other_pending_slots(rig):
    runner, clock, calls = rig
    runner.schedule()
    clock.advance(days=1)
    run = runner.run_due(name='refresh:AAPL')['runs'][0]
    assert run['scheduled_at'] == '2024-12-03T21:30:00+00:00'
    assert len(calls) == 1
    runner.run_due()
    assert len(calls) == 1
    assert runner.store.runs()[0]['status'] == 'skipped'
