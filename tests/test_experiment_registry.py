"""Immutable research control: offline SQL, fault injection and real tiny estimators."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from stock_app.research.registry import Registry
from stock_app.research.registry_admin import audit, import_legacy
from stock_app.research.registry_execution import execute, prepare, replay
from stock_app.research.registry_cli import read_rows
from stock_app.research.store import ResearchStore
from stock_app.research.experiments import run_experiment, _Checkpoints
from test_research_experiments import history, small_config


@pytest.fixture
def registry(tmp_path):
    return Registry(ResearchStore(tmp_path / 'research' / 'research.sqlite3'),
                    clock=lambda: '2026-09-22T00:00:00+00:00')


@pytest.fixture
def spec(registry, tmp_path):
    return prepare(registry, history(), options(tmp_path))[0]


def options(tmp_path, **changes):
    return dict(ticker='TEST', output=tmp_path / 'engine', feature_sets=('baseline',),
                config=small_config(), **changes)


def start(registry, spec, key='manual:one'):
    run = registry.run(spec_id=spec['id'], execution_key=key)
    return run, registry.begin(run['id'])


def report(**changes):
    return {'status': 'completed', 'aggregate': {'brier': .3}, 'folds': [
        {'comparisons': {'training_prior': {'mean_improvement': -.05, 'lower': -.1, 'upper': .03}}}], **changes}


def test_question_creation_and_parent_revision(registry):
    q = registry.question(title='A useful question', description='A reproducible research question')
    child = registry.question(title='A revised question', description='Different scope is a child question', parent_id=q['id'])
    assert child['parent_id'] == q['id'] and child['id'] != q['id']
    assert q['created_at'] == '2026-09-22T00:00:00+00:00'


def test_hypothesis_creation_fingerprint_and_validation(registry, spec):
    h = registry.get('hypotheses', spec['hypothesis_id'])
    doc = h['document']
    kwargs = {k: doc[k] for k in ('statement', 'effect', 'rationale', 'falsification')}
    assert registry.hypothesis(question_id=h['question_id'], **kwargs)['id'] == h['id']
    with pytest.raises(ValueError):
        registry.hypothesis(question_id=h['question_id'], **{**kwargs, 'falsification': 'try'})


def test_spec_canonical_order(registry, spec):
    contract = dict(reversed(list(spec['document']['contract'].items())))
    duplicate = registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=contract)
    assert duplicate['id'] == spec['id']
    assert len(registry.list('specs')) == 1


def test_duplicate_proposals_are_retained(registry, spec):
    before = len(registry.list('events'))
    registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=spec['document']['contract'])
    assert len(registry.list('events')) == before + 1


@pytest.mark.parametrize('field,value', [('primary_metric', 'different_loss'), ('randomness', {'seed': 99}),
                                        ('target', {'horizon': 20}), ('data', {'new': {'sha256': 'changed'}})])
def test_identity_change_creates_new_spec(registry, spec, field, value):
    contract = deepcopy(spec['document']['contract'])
    contract[field] = value
    changed = registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=contract)
    assert changed['fingerprint'] != spec['fingerprint']


@pytest.mark.parametrize('kind', ['questions', 'hypotheses', 'families', 'specs', 'trials', 'runs', 'attempts', 'outcomes', 'events', 'artifacts', 'model_links'])
def test_sql_history_cannot_be_updated_or_deleted(registry, spec, kind):
    run, attempt = start(registry, spec)
    file = registry.store.path.parent / 'model.bin'
    file.write_bytes(b'model')
    artifact = {'role': 'candidate', 'path': str(file), 'sha256': sha256(b'model').hexdigest()}
    registry.finish(attempt['id'], state='completed', artifacts=[artifact],
                    candidate={'id': artifact['sha256'], 'state': 'candidate', 'artifact': str(file)})
    for operation in (f'UPDATE research_{kind} SET created_at=created_at', f'DELETE FROM research_{kind}'):
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with registry.store.connection() as db:
                db.execute(operation)


def test_trial_family_and_equivalent_parameters(registry, spec):
    family = registry.get('families', spec['family_id'])
    assert len(family['document']['dimensions']['window']) == 2
    trial = registry.list('trials')[0]
    other = registry.trial(family_id=spec['family_id'], parameters=dict(reversed(list(trial['document'].items()))))
    assert other['id'] == trial['id']
    with pytest.raises(ValueError):
        registry.trial(family_id=spec['family_id'], parameters={**trial['document'], 'window': 1})


def test_run_registration_and_execution_dedup(registry, spec):
    first = registry.run(spec_id=spec['id'], execution_key='schedule:session')
    second = registry.run(spec_id=spec['id'], execution_key='schedule:session')
    assert first['id'] == second['id'] and registry.state(first['id']) == 'registered'


def test_run_rejects_different_spec_for_same_execution(registry, spec):
    registry.run(spec_id=spec['id'], execution_key='schedule:session')
    contract = deepcopy(spec['document']['contract'])
    contract['randomness']['seed'] += 1
    other = registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=contract)
    with pytest.raises(ValueError, match='pinned'):
        registry.run(spec_id=other['id'], execution_key='schedule:session')


@pytest.mark.parametrize('state', ['failed', 'invalid', 'aborted'])
def test_failed_invalid_aborted_attempts_survive_retry(registry, spec, state):
    run, attempt = start(registry, spec)
    registry.finish(attempt['id'], state=state, evidence={'error': 'retained'})
    with pytest.raises(ValueError):
        registry.begin(run['id'])
    second = registry.begin(run['id'], resume=True)
    registry.finish(second['id'], state='completed', metrics={'brier': .4})
    assert [o['state'] for o in registry.history()[0]['outcomes']] == [state, 'completed']
    assert second['number'] == 2 and len(registry.list('runs')) == 1


def test_completed_cannot_restart_python_or_sql(registry, spec):
    run, attempt = start(registry, spec)
    registry.finish(attempt['id'], state='completed')
    with pytest.raises(ValueError):
        registry.begin(run['id'], resume=True)
    with pytest.raises(sqlite3.IntegrityError, match='completed'):
        with registry.store.connection() as db:
            db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,?)', ('new', run['id'], 2, 'now', '{}'))


def test_running_cannot_get_second_attempt(registry, spec):
    run, _ = start(registry, spec)
    with pytest.raises(ValueError):
        registry.begin(run['id'], resume=True)


def test_interruption_requires_explicit_reconciliation(registry, spec):
    run, _ = start(registry, spec)
    registry.reconcile(run['id'], reason='Worker confirmed dead', actor='local operator')
    assert registry.state(run['id']) == 'blocked'
    with pytest.raises(ValueError):
        registry.begin(run['id'], resume=True)
    registry.reconcile(run['id'], reason='Verified inputs and checkpoint checksums', actor='local operator')
    assert registry.begin(run['id'], resume=True)['number'] == 3


@pytest.mark.parametrize('state', ['queued', 'paused', 'active', 'supported'])
def test_invalid_state_rejected(registry, spec, state):
    _, attempt = start(registry, spec)
    with pytest.raises(ValueError):
        registry.finish(attempt['id'], state=state)


def test_negative_result_and_declared_metrics(registry, tmp_path):
    execute(lambda *a, **k: report(), history(), registry=registry, **options(tmp_path))
    outcome = registry.list('outcomes')[0]
    assert outcome['disposition'] == 'not_supported' and outcome['primary_metric'] == 'brier'
    assert outcome['document']['metrics'] == {'brier': .3}


def test_exploratory_metrics_cannot_replace_primary(registry, spec):
    _, attempt = start(registry, spec)
    with pytest.raises(ValueError, match='exploratory'):
        registry.finish(attempt['id'], state='completed', metrics={'after_the_fact': 1})
    result = registry.finish(attempt['id'], state='completed', metrics={'brier': .2}, exploratory={'after_the_fact': 1})
    assert result['primary_metric'] == 'brier' and result['document']['exploratory']['after_the_fact'] == 1


def test_sql_primary_cannot_be_replaced(registry, spec):
    run, attempt = start(registry, spec)
    with pytest.raises(sqlite3.IntegrityError, match='primary'):
        with registry.store.connection() as db:
            db.execute('INSERT INTO research_outcomes VALUES(?,?,?,?,?,?,?,?)',
                       ('bad', run['id'], attempt['id'], 'now', 'completed', 'supported', 'other', '{}'))


def test_nonfinite_metrics_rejected(registry, spec):
    _, attempt = start(registry, spec)
    with pytest.raises(ValueError):
        registry.finish(attempt['id'], state='completed', metrics={'brier': float('nan')})


def test_data_provenance_and_path_independent_spec(registry, tmp_path):
    a, refs, _ = prepare(registry, history(), options(tmp_path / 'a'))
    b, _, _ = prepare(registry, history(), options(tmp_path / 'b'))
    assert a['fingerprint'] == b['fingerprint']
    assert a['document']['contract']['data']['history']['frame_sha256']
    assert Path(refs['history']['path']).exists()


def test_candidate_link_and_lifecycle_preserved(registry, tmp_path):
    path = tmp_path / 'candidate.bin'
    path.write_bytes(b'candidate')
    model_id = sha256(path.read_bytes()).hexdigest()
    registry.store.put('models', {'id': model_id, 'state': 'shadow', 'frozen': 'old', 'symbol': 'TEST', 'task': 'binary'})
    execute(lambda *a, **k: report(candidate={'path': str(path)}), history(), registry=registry,
            job_id='fixture', **options(tmp_path))
    assert registry.store.get('models', model_id)['state'] == 'shadow'
    assert registry.store.get('models', model_id)['frozen'] == 'old'
    link = registry.list('model_links')[0]
    assert registry.get('runs', link['run_id']) and registry.get('artifacts', link['artifact_id'])
    assert registry.store.active('TEST', 'binary') is None


def test_legacy_candidate_without_link_remains_valid(registry):
    registry.store.put('models', {'id': 'old', 'state': 'candidate'})
    assert audit(registry.store.path)['candidates_without_provenance'] == ['old']
    assert registry.store.get('models', 'old')['state'] == 'candidate'


def test_legacy_import_preserves_original_without_preregistration(registry):
    old = registry.store.put('jobs', {'id': 'old', 'kind': 'experiment', 'state': 'failed', 'error': 'failure', 'parameters': {'task': 'binary'}})
    preview = import_legacy(registry)
    assert preview['imported'] == 1 and registry.list('runs') == []
    result = import_legacy(registry, apply=True)
    assert result['imported'] == 1
    spec = registry.list('specs')[0]
    assert spec['registration_mode'] == 'legacy_import' and spec['hypothesis_id'] is None and spec['primary_metric'] is None
    assert registry.store.get('jobs', 'old') == old
    assert registry.history()[0]['outcomes'][0]['state'] == 'failed'


def test_legacy_import_idempotency(registry):
    registry.store.put('jobs', {'id': 'old', 'kind': 'experiment', 'state': 'completed'})
    import_legacy(registry, apply=True)
    again = import_legacy(registry, apply=True)
    assert again['already_registered'] == 1 and len(registry.list('attempts')) == 1


def test_legacy_ambiguous_artifact_not_inferred(registry):
    registry.store.put('jobs', {'id': 'old', 'kind': 'experiment', 'state': 'completed'})
    registry.store.put('models', {'id': 'model', 'artifact': '/no/such/file', 'metadata': {'job_id': 'old'}})
    result = import_legacy(registry, apply=True)
    assert len(result['ambiguous']) == 1 and result['candidate_links'] == 0


def test_crash_before_result_commit_keeps_attempt(registry, tmp_path):
    def crash(*args, **kwargs):
        raise RuntimeError('fit failed')
    with pytest.raises(RuntimeError):
        execute(crash, history(), registry=registry, **options(tmp_path))
    assert registry.history()[0]['state'] == 'failed'
    execute(lambda *a, **k: report(), history(), registry=registry, **options(tmp_path))
    assert len(registry.list('runs')) == 1 and len(registry.list('attempts')) == 2


def test_crash_after_artifact_write_and_link_failure_is_atomic(registry, tmp_path):
    candidate = tmp_path / 'candidate.bin'
    def engine(*args, **kwargs):
        candidate.write_bytes(b'candidate')
        return report(candidate={'path': str(candidate)})
    with registry.store.connection() as db:
        db.execute("CREATE TRIGGER fail_link BEFORE INSERT ON research_model_links BEGIN SELECT RAISE(ABORT,'injected link failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        execute(engine, history(), registry=registry, job_id='fixture', **options(tmp_path))
    assert candidate.exists() and registry.store.list('models') == []
    assert registry.list('artifacts') == [] and registry.history()[0]['state'] == 'blocked'
    assert any(i['kind'] == 'uncommitted_artifact' for i in audit(registry.store.path)['issues'])


def test_missing_candidate_records_blocked_outcome(registry, tmp_path):
    with pytest.raises(FileNotFoundError):
        execute(lambda *a, **k: report(candidate={'path': str(tmp_path / 'missing')}), history(), registry=registry, **options(tmp_path))
    assert registry.history()[0]['state'] == 'blocked'


def test_audit_detects_orphans_and_spec_tampering(registry, spec):
    with registry.store.connection() as db:
        db.execute('DROP TRIGGER research_specs_update')
        db.execute("UPDATE research_specs SET document='{}' WHERE id=?", (spec['id'],))
    result = audit(registry.store.path)
    assert any(i['kind'] == 'mutated_specs' for i in result['issues'])


def test_audit_is_read_only(registry, spec):
    with registry.store.connection() as db:
        before = '\n'.join(db.iterdump())
    result = audit(registry.store.path)
    with registry.store.connection() as db:
        after = '\n'.join(db.iterdump())
    assert before == after and result['counts']['specs'] == 1


def test_history_includes_all_outcomes(registry, spec):
    for state in ['completed', 'failed', 'invalid', 'aborted', 'blocked']:
        _, attempt = start(registry, spec, state)
        registry.finish(attempt['id'], state=state)
    assert {r['state'] for r in registry.history()} == {'completed', 'failed', 'invalid', 'aborted', 'blocked'}
    assert len(read_rows(registry.store.path)) == 5


@pytest.mark.parametrize('task', ['binary', 'regression'])
def test_real_engine_preregistration_trial_attempts_and_metrics(registry, tmp_path, task):
    result = execute(run_experiment, history(), registry=registry, **options(tmp_path, task=task))
    assert result['status'] == 'completed'
    spec = registry.list('specs')[0]
    assert spec['primary_metric'] == ('brier' if task == 'binary' else 'mae')
    assert len(registry.list('trials')) == 2
    events = registry.list('events')
    tested = {e['trial_id'] for e in events if e['kind'] == 'fit_started' and e['trial_id']}
    assert tested == {t['id'] for t in registry.list('trials')}
    assert all(registry.get('attempts', e['attempt_id']) for e in events if e['kind'] == 'fit_started')
    assert registry.list('artifacts') and registry.history()[0]['state'] == 'completed'


def test_failed_parameter_variant_retained(registry, tmp_path, monkeypatch):
    from stock_app.research import experiments
    monkeypatch.setattr(experiments, '_fit_xgb', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('bad variant')))
    with pytest.raises(RuntimeError):
        execute(run_experiment, history(), registry=registry, **options(tmp_path))
    failures = [e for e in registry.list('events') if e['kind'] == 'fit_failed']
    assert failures[0]['trial_id'] and len(registry.list('trials')) == 2
    assert registry.history()[0]['state'] == 'failed'


def test_offline_replay_matches_metrics(registry, tmp_path):
    result = execute(run_experiment, history(), registry=registry, **options(tmp_path))
    run = registry.list('runs')[0]
    replayed = replay(registry, run['id'], tmp_path / 'replayed')
    assert result['aggregate'] == replayed['aggregate']
    assert result['folds'] == replayed['folds']
    assert len(registry.list('runs')) == 2 and len(registry.list('specs')) == 1


def test_replay_rejects_changed_input(registry, tmp_path):
    execute(lambda *a, **k: report(), history(), registry=registry, **options(tmp_path))
    run = registry.list('runs')[0]
    Path(run['document']['inputs']['history']['path']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        replay(registry, run['id'], tmp_path / 'replay')


def test_concurrent_spec_registration(registry, spec):
    def register(_):
        return registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=spec['document']['contract'])['id']
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(register, range(8))) == {spec['id']}
    assert len(registry.list('specs')) == 1


def test_concurrent_trial_registration(registry, spec):
    trial = registry.list('trials')[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: registry.trial(family_id=spec['family_id'], parameters=trial['document'])['id'], range(8)))
    assert set(ids) == {trial['id']} and len(registry.list('trials')) == 2


def test_sql_foreign_key_and_fingerprint_uniqueness(registry, spec):
    with pytest.raises(sqlite3.IntegrityError):
        with registry.store.connection() as db:
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?)', ('orphan', 'missing', 'key', 'now', 'manual', '{}'))
    with pytest.raises(sqlite3.IntegrityError):
        with registry.store.connection() as db:
            db.execute('INSERT INTO research_specs SELECT ?,fingerprint,created_at,hypothesis_id,family_id,registration_mode,primary_metric,document FROM research_specs WHERE id=?', ('copy', spec['id']))


def test_readonly_cli_and_api(registry, spec, capsys):
    from stock_app.research.registry_cli import main
    assert main(['--database', str(registry.store.path), 'audit']) == 0
    assert json.loads(capsys.readouterr().out)['counts']['specs'] == 1
    from stock_app.app import app
    client = app.test_client()
    response = client.get('/api/research/registry/specs')
    assert response.status_code == 200 and response.json[0]['id'] == spec['id']
    assert client.post('/api/research/registry/specs', json={}).status_code == 405


def test_scheduler_candidate_uses_one_research_run(tmp_path, monkeypatch):
    from stock_app.jobs import JobService
    from stock_app.research import experiments
    from test_operational_jobs import Clock, history as scheduler_history
    service = JobService(tmp_path / 'research', clock=Clock())
    for symbol in ('AAPL', 'SPY', 'QQQ', 'XLK'):
        service.runtime.data.ingest(symbol, scheduler_history(), now=Clock()())
    candidate = tmp_path / 'candidate.bin'
    candidate.write_bytes(b'candidate')
    monkeypatch.setattr(experiments, 'run_experiment', lambda *a, **k: report(candidate={'path': str(candidate)}))
    first = service.runner.run_due(name='experiment:AAPL:binary')['runs'][0]
    registry = Registry(service.runtime.store)
    assert first['status'] == 'succeeded' and registry.list('runs')[0]['origin'] == 'scheduled'
    assert service.runner.run_due(name='experiment:AAPL:binary')['runs'] == []
    assert len(registry.list('runs')) == len(registry.list('attempts')) == 1


def test_timeout_and_cancellation_preserve_terminal_history(registry, tmp_path):
    execute(run_experiment, history(), registry=registry, **options(tmp_path, time_budget=0))
    assert registry.history()[0]['state'] == 'aborted'
    assert registry.list('outcomes')[0]['document']['evidence']['engine_status'] == 'paused'


def test_exact_completed_repeat_creates_no_new_attempt(registry, tmp_path):
    first = execute(lambda *a, **k: report(), history(), registry=registry, **options(tmp_path))
    second = execute(lambda *a, **k: pytest.fail('must not run'), history(), registry=registry, **options(tmp_path))
    assert first == second and len(registry.list('attempts')) == 1


def test_context_and_history_have_separate_fingerprints(registry, tmp_path):
    data = history()
    changed = data.copy()
    changed.iloc[-1, 0] += 1
    spec, _, _ = prepare(registry, data, options(tmp_path, contexts={'TEST': changed}))
    assert spec['document']['contract']['data']['history'] != spec['document']['contract']['data']['context:TEST']


def test_explicit_registered_hypothesis_is_executed(registry, spec, tmp_path):
    from stock_app.research.registry_execution import execute_spec
    question = registry.question(title='Custom explicit research question', description='User proposed fixed candidate comparison')
    hypothesis = registry.hypothesis(question_id=question['id'], statement='The fixed procedure lowers Brier loss against training prior.',
        effect='Lower Brier loss', rationale='Test the specified user proposal', falsification='No Brier loss improvement on declared blocks')
    family = registry.family(hypothesis_id=hypothesis['id'], dimensions=registry.get('families', spec['family_id'])['document']['dimensions'])
    custom = registry.spec(hypothesis_id=hypothesis['id'], family_id=family['id'], contract=spec['document']['contract'])
    _, refs, _ = prepare(registry, history(), options(tmp_path))
    execute_spec(registry, custom['id'], refs, tmp_path / 'custom', execution_key='custom:one')
    assert registry.list('runs')[0]['spec_id'] == custom['id']
    assert registry.history()[0]['state'] == 'completed'


def test_registered_contract_mismatch_never_evaluates(registry, spec, tmp_path):
    with pytest.raises(ValueError, match='does not match'):
        execute(lambda *a, **k: pytest.fail('must not evaluate'), history(), registry=registry,
                registered_spec_id=spec['id'], **options(tmp_path, task='regression'))
    assert registry.list('attempts') == []


def test_no_false_preregistration_of_preexisting_completed_report(registry, tmp_path):
    output = options(tmp_path)['output']
    output.mkdir()
    (output / 'report.json').write_text(json.dumps(report()))
    with pytest.raises(ValueError, match='predates'):
        execute(lambda *a, **k: pytest.fail('must not evaluate'), history(), registry=registry, **options(tmp_path))
    assert registry.history()[0]['state'] == 'invalid'


def test_volatiles_rejected_in_specification(registry, spec):
    contract = deepcopy(spec['document']['contract'])
    contract['model']['path'] = '/tmp/temporary'
    with pytest.raises(ValueError, match='volatile'):
        registry.spec(hypothesis_id=spec['hypothesis_id'], family_id=spec['family_id'], contract=contract)


def test_audit_detects_foreign_key_orphan(registry):
    with sqlite3.connect(registry.store.path) as db:
        db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?)', ('bad', 'missing', 'key', 'now', 'manual', '{}'))
    assert any(i['kind'] == 'foreign_key' for i in audit(registry.store.path)['issues'])


def test_legacy_verified_candidate_link_without_rewriting_model(registry, tmp_path):
    candidate = tmp_path / 'old.bin'
    candidate.write_bytes(b'old-model')
    registry.store.put('jobs', {'id': 'old-job', 'kind': 'experiment', 'state': 'completed'})
    model = registry.store.put('models', {'id': 'old-model', 'artifact': str(candidate), 'artifact_sha256': sha256(candidate.read_bytes()).hexdigest(),
                                        'metadata': {'job_id': 'old-job'}, 'state': 'shadow'})
    assert import_legacy(registry, apply=True)['candidate_links'] == 1
    assert registry.store.get('models', 'old-model') == model
    assert audit(registry.store.path)['issues'] == []


def test_pre_schema_readers_do_not_create_database(tmp_path):
    path = tmp_path / 'missing.sqlite3'
    assert audit(path)['status'] == 'migration_required'
    assert read_rows(path) == [] and not path.exists()


def test_ancillary_failure_is_registered(tmp_path, monkeypatch):
    from stock_app.training.train_baselines import run_research
    from stock_app.research.registry_execution import default_registry
    with pytest.raises(ValueError):
        run_research(history(), 'TEST', tmp_path / 'baseline', decision_threshold=2)
    registry = default_registry()
    assert registry.history()[0]['state'] == 'invalid'
    assert registry.list('specs')[0]['document']['contract']['model']['procedure'] == 'run_research'


def test_exploratory_feature_addendum_has_separate_contract(tmp_path):
    from stock_app.research.experiments import enrich_feature_comparison
    from stock_app.research.registry_execution import default_registry
    run_experiment(history(), **options(tmp_path))
    enrich_feature_comparison(history(), output=options(tmp_path)['output'], time_budget=0)
    registry = default_registry()
    assert len(registry.list('runs')) == 2
    exploratory = next(r for r in registry.list('runs') if r['origin'] == 'exploratory')
    contract = registry.get('specs', exploratory['spec_id'])['document']['contract']
    assert contract['evaluation']['analysis_kind'] == 'exploratory'
    assert contract['target']['horizon'] == 5 and contract['primary_metric'] == 'brier'
