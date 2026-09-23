"""Offline evidence, deterministic replay, operator transactions and boundary tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from stock_app.research.forecast_identity import canonical_json, digest
from stock_app.research.integrity import get_evidence, verify_run
from stock_app.research.integrity_audit import audit_integrity
from stock_app.research.integrity_cli import main
from stock_app.research.integrity_reconcile import detect_cases, findings, list_cases, resolve
from stock_app.research.integrity_replay import POLICY, compare, exclusive, reproduction_history, request_reproduction
from stock_app.research.registry import Registry, TABLES
from stock_app.research.registry_execution import execute, prepare
from stock_app.research.store import ResearchStore
from test_research_experiments import history, small_config

NOW = '2026-09-23T00:00:00+00:00'
LATER = '2026-09-24T00:00:00+00:00'


@pytest.fixture
def registry(tmp_path):
    return Registry(ResearchStore(tmp_path/'research.sqlite3'), clock=lambda:NOW)


def options(tmp_path, task='binary'):
    return dict(ticker='TEST', output=tmp_path/'original', feature_sets=('baseline',), config=small_config(), task=task)


def fake_engine(frame, **opts):
    output = Path(opts['output'])
    output.mkdir(exist_ok=True, parents=True)
    file = output/'candidate.joblib'
    file.write_bytes(b'fixed candidate bytes')
    metric = 'brier' if opts['task'] == 'binary' else 'mae'
    result = {'status':'completed', 'aggregate':{metric: .3}, 'folds':[{'observations':[{'prediction':.4}], 'baselines':{'prior':.32}}],
              'candidate': {'path':str(file), 'artifact_sha256':sha256(file.read_bytes()).hexdigest()}}
    (output/'report.json').write_text(canonical_json(result))
    return result


@pytest.fixture
def completed(registry, tmp_path):
    execute(fake_engine, history(), registry=registry, execution_key='one', **options(tmp_path))
    return registry.list('runs')[0]['id']


def replay_fake(path, manifest, output):
    return fake_engine(None, output=output, task=manifest['spec']['document']['contract']['task'])


def snapshot(registry):
    with registry.store.connection() as db:
        return {k:[tuple(r) for r in db.execute('SELECT * FROM research_'+k)] for k in TABLES}


def check(report, prefix):
    return [v for v in report['checks'] if v['reference'].startswith(prefix)]


def tamper(registry, table, sql, params=()):
    with registry.store.connection() as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('DROP TRIGGER IF EXISTS research_'+table+'_update')
        db.execute('DROP TRIGGER IF EXISTS research_'+table+'_delete')
        db.execute(sql, params)


def test_manifest_has_entire_chain(registry, completed):
    value = get_evidence(registry.store.path, completed)
    assert all(value['manifest'][k] for k in ('run','spec','question','hypothesis','family','trials','attempts','outcomes','artifacts'))
    assert value['fingerprint'] == digest(value['manifest'])


def test_manifest_is_deterministic(registry, completed):
    assert get_evidence(registry.store.path, completed) == get_evidence(registry.store.path, completed)


def test_manifest_excludes_volatile_observation_and_duplicate_events(registry, completed):
    before = get_evidence(registry.store.path, completed)
    registry.event('duplicate_execution', {'time':'later'}, run_id=completed)
    assert get_evidence(registry.store.path, completed) == before
    assert verify_run(registry.store.path, completed, clock=lambda:NOW)['manifest_fingerprint'] == verify_run(registry.store.path, completed, clock=lambda:LATER)['manifest_fingerprint']


def test_artifact_review_verified(registry, completed):
    value = verify_run(registry.store.path, completed, 'artifact')
    assert value['status'] == 'verified', value


@pytest.mark.parametrize('operation,status', [('delete','missing'),('change','mismatch')])
def test_artifact_failure(registry, completed, operation, status):
    artifact = next(a for a in registry.list('artifacts') if a['role']=='candidate')
    p = Path(artifact['path'])
    p.unlink() if operation == 'delete' else p.write_bytes(b'changed')
    value = verify_run(registry.store.path, completed, 'artifact')
    assert check(value, 'artifact:'+artifact['id'])[0]['status'] == status


@pytest.mark.parametrize('operation,status', [('delete','missing'),('change','mismatch')])
def test_dataset_failure(registry, completed, operation, status):
    p = Path(registry.get('runs', completed)['document']['inputs']['history']['path'])
    p.unlink() if operation == 'delete' else p.write_bytes(b'changed')
    assert check(verify_run(registry.store.path, completed, 'artifact'),'input:history')[0]['status'] == status


def test_dataset_frame_fingerprint_mismatch(registry, completed):
    spec = registry.list('specs')[0]
    spec['document']['contract']['data']['history']['frame_sha256'] = 'wrong'
    tamper(registry,'specs','UPDATE research_specs SET document=?',(canonical_json(spec['document']),))
    assert check(verify_run(registry.store.path, completed,'artifact'),'input:history')[0]['status']=='mismatch'


def test_broken_provenance(registry, completed):
    tamper(registry,'hypotheses','UPDATE research_hypotheses SET question_id=?',('absent',))
    assert check(verify_run(registry.store.path, completed),'question')[0]['status']=='missing'


def test_metric_spec_linkage(registry, completed):
    tamper(registry,'outcomes','UPDATE research_outcomes SET primary_metric=?',('wrong',))
    assert check(verify_run(registry.store.path, completed),'result.metrics:')[0]['status']=='mismatch'


def test_chronology(registry, completed):
    tamper(registry,'outcomes','UPDATE research_outcomes SET created_at=?',('2020-01-01T00:00:00+00:00',))
    assert check(verify_run(registry.store.path, completed),'chronology:result:')[0]['status']=='mismatch'


def legacy(registry):
    from stock_app.research.registry_admin import import_legacy
    registry.store.put('jobs', {'id':'old-job','kind':'experiment','state':'completed','progress':{'status':'completed'}})
    import_legacy(registry, apply=True)
    return next(r['id'] for r in registry.list('runs') if r['origin']=='legacy_import')


def test_legacy_evidence_honesty(registry):
    run = legacy(registry)
    result = verify_run(registry.store.path, run, 'artifact')
    assert result['status']=='incomplete'
    assert check(result,'question')[0]['status']=='unverifiable'


def test_metadata_does_not_read_artifact_bytes(registry, completed):
    with patch.object(Path,'read_bytes',side_effect=AssertionError('disk bytes forbidden')):
        assert verify_run(registry.store.path, completed)['status']=='incomplete'


def test_review_read_only_and_certificate(registry, completed):
    before = snapshot(registry)
    a = verify_run(registry.store.path, completed, 'artifact', clock=lambda:NOW)
    b = verify_run(registry.store.path, completed, 'artifact', clock=lambda:NOW)
    assert a==b and snapshot(registry)==before
    assert a['fingerprint']==digest({k:v for k,v in a.items() if k!='fingerprint'})


@pytest.mark.parametrize('delta,status',[(0.,'exact'),(1e-13,'equivalent'),(.05,'different')])
def test_fixed_float_policy(delta,status):
    assert compare({'primary_metric':.63},{'primary_metric':.63+delta})['status']==status


@pytest.mark.parametrize('field', ['config','fingerprints','selection','boundaries','seed','artifact_sha256'])
def test_identity_fields_never_tolerated(field):
    assert compare({field:.3},{field:.3+1e-13})['status']=='different'


@pytest.mark.parametrize('field', ['primary_metric','secondary_metric','baseline','prediction_vector','bootstrap_ci'])
def test_numeric_comparison_fields(field):
    values = [.4,.5] if field in {'prediction_vector','bootstrap_ci'} else .4
    other = [.4,.7] if isinstance(values,list) else .7
    report = compare({field:values},{field:other})
    assert report['status']=='different' and any(c['path'].startswith(field) for c in report['comparisons'])


def test_policy_version_and_no_caller_tolerance():
    assert POLICY['version']=='float64-v1'
    with pytest.raises(TypeError):
        compare(.63,.58,tolerance=.1)


def test_reproduction_exact_no_new_research(registry, completed):
    before = snapshot(registry)
    with patch('stock_app.research.integrity_replay._execute', side_effect=replay_fake):
        result = request_reproduction(registry.store, completed, request_key='exact', clock=lambda:NOW)
    assert result['status']=='exact', result
    assert snapshot(registry)==before
    assert result['document']['policy']==POLICY


def test_reproduction_equivalent(registry, completed):
    def slightly(path,m,output):
        report = replay_fake(path,m,output)
        report['aggregate']['brier'] += 1e-13
        return report
    with patch('stock_app.research.integrity_replay._execute',side_effect=slightly):
        result = request_reproduction(registry.store,completed,request_key='roundoff')
    assert result['status']=='equivalent'


def test_reproduction_model_bytes_exact(registry, completed):
    def changed(path,m,output):
        result = replay_fake(path,m,output)
        Path(result['candidate']['path']).write_bytes(b'new model')
        return result
    with patch('stock_app.research.integrity_replay._execute',side_effect=changed):
        result=request_reproduction(registry.store,completed,request_key='model-different')
    assert result['status']=='different'
    assert any(c['path']=='artifact.sha256' and c['status']=='different' for c in result['result']['document']['comparisons'])


def test_missing_replay_input_blocked(registry, completed):
    Path(registry.get('runs',completed)['document']['inputs']['history']['path']).unlink()
    assert request_reproduction(registry.store,completed,request_key='missing')['status']=='unavailable'


def test_failed_replay_retained(registry, completed):
    with patch('stock_app.research.integrity_replay._execute',side_effect=RuntimeError('failed engine')):
        result=request_reproduction(registry.store,completed,request_key='failure')
    assert result['status']=='failed' and result['result']['document']['error_type']=='RuntimeError'


def test_legacy_replay_unavailable(registry):
    assert request_reproduction(registry.store,legacy(registry),request_key='legacy')['status']=='unavailable'


def test_code_drift_unavailable(registry, completed):
    with patch('stock_app.research.registry_execution.code_manifest',return_value={'sha256':'changed','python':'x','dependencies':{}}):
        assert request_reproduction(registry.store,completed,request_key='drift')['status']=='unavailable'


def test_same_request_is_idempotent(registry, completed):
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake) as engine:
        a=request_reproduction(registry.store,completed,request_key='same')
        b=request_reproduction(registry.store,completed,request_key='same')
    assert a==b and engine.call_count==1


def test_crash_retry_retains_unfinished_check(registry, completed):
    with patch('stock_app.research.integrity_replay._execute',side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            request_reproduction(registry.store,completed,request_key='crash')
    assert reproduction_history(registry.store.path)[0]['status']=='running'
    assert request_reproduction(registry.store,completed,request_key='crash')['status']=='blocked'
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake):
        assert request_reproduction(registry.store,completed,request_key='explicit-new-check')['status']=='exact'
    assert len(registry.list('runs'))==1 and len(reproduction_history(registry.store.path))==2


def test_verification_write_failure_leaves_start(registry, completed):
    with registry.store.connection() as db:
        db.execute("CREATE TRIGGER inject_failure BEFORE INSERT ON integrity_results BEGIN SELECT RAISE(ABORT,'write failure'); END")
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake):
        with pytest.raises(sqlite3.IntegrityError):
            request_reproduction(registry.store,completed,request_key='write-failed')
    assert reproduction_history(registry.store.path)[0]['status']=='running'


def interrupted(registry,tmp_path):
    spec,refs,_=prepare(registry,history(),options(tmp_path))
    run=registry.run(spec_id=spec['id'],execution_key='interrupted',provenance={'inputs':refs,'output':str(tmp_path/'interrupted')})
    registry.begin(run['id'])
    return run['id']


def case_for(registry,category):
    return next(c for c in detect_cases(registry.store,clock=lambda:LATER) if c['category']==category)


def action(registry,case,name,**kwargs):
    return resolve(registry.store,case['id'],action=name,actor='local operator',reason='Reviewed immutable evidence',clock=lambda:LATER,**kwargs)


def test_stale_run_detected_no_auto_failure(registry,tmp_path):
    run=interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    assert c['document']['related']['run_id']==run and registry.state(run)=='running'
    assert c['document']['evidence']['classification']=='no_recorded_artifact_or_result'


def test_live_age_not_stale(registry,tmp_path):
    interrupted(registry,tmp_path)
    assert not any(f['category']=='stale_running' for f in findings(registry.store.path,clock=lambda:NOW))


def test_orphan_file_detected(registry,tmp_path):
    run=interrupted(registry,tmp_path)
    output=tmp_path/'interrupted'
    output.mkdir()
    (output/'candidate.joblib').write_bytes(b'orphan')
    assert case_for(registry,'orphan_artifact')['document']['related']['run_id']==run
    assert case_for(registry,'stale_running')['document']['evidence']['classification']=='unlinked_candidate'


@pytest.mark.parametrize('missing,category',[(True,'missing_artifact'),(False,'checksum_mismatch')])
def test_artifact_cases(registry,completed,missing,category):
    a=next(a for a in registry.list('artifacts') if a['role']=='candidate')
    p=Path(a['path'])
    p.unlink() if missing else p.write_bytes(b'bad')
    assert case_for(registry,category)['state']=='open'


def test_duplicate_case_detection(registry,tmp_path):
    interrupted(registry,tmp_path)
    assert detect_cases(registry.store,clock=lambda:LATER)==detect_cases(registry.store,clock=lambda:LATER)


def test_case_transitions_and_history(registry,tmp_path):
    interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    assert action(registry,c,'begin_review')['state']=='under_review'
    assert action(registry,c,'manual_evidence_added',evidence={'note':'inspected'})['state']=='under_review'
    assert action(registry,c,'dismissed_false_positive',evidence={'note':'worker live'})['state']=='dismissed'
    assert len(list_cases(registry.store.path,c['id'])['actions'])==3
    with pytest.raises(ValueError):
        action(registry,c,'begin_review')


def test_unknown_action_refused(registry,tmp_path):
    interrupted(registry,tmp_path)
    with pytest.raises(ValueError):
        action(registry,case_for(registry,'stale_running'),'arbitrary_sql')


def test_typed_interruption_resolution(registry,tmp_path):
    run=interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    action(registry,c,'run_marked_blocked',evidence={'worker_confirmed_dead':True})
    assert registry.state(run)=='blocked'
    action(registry,c,'retry_authorized',evidence={'worker_confirmed_dead':True,'partial_effects_reviewed':True})
    assert registry.state(run)=='aborted' and len(registry.list('outcomes'))==2
    assert list_cases(registry.store.path,c['id'])['state']=='resolved'


def test_resolution_preconditions(registry,tmp_path):
    interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    with pytest.raises(ValueError):
        action(registry,c,'run_marked_blocked')
    with pytest.raises(ValueError):
        action(registry,c,'retry_authorized',evidence={'worker_confirmed_dead':True})
    assert list_cases(registry.store.path,c['id'])['state']=='open'


def test_resolution_refuses_worker_lock(registry,tmp_path):
    interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    with exclusive(registry.store.path.parent/'worker.lock'):
        with pytest.raises(ValueError,match='Worker lock'):
            action(registry,c,'begin_review')


def test_reconciliation_transaction_rollback(registry,tmp_path):
    run=interrupted(registry,tmp_path)
    c=case_for(registry,'stale_running')
    with registry.store.connection() as db:
        db.execute("CREATE TRIGGER inject_action BEFORE INSERT ON integrity_actions BEGIN SELECT RAISE(ABORT,'fail action'); END")
    with pytest.raises(sqlite3.IntegrityError):
        action(registry,c,'run_marked_blocked',evidence={'worker_confirmed_dead':True})
    assert registry.state(run)=='running' and list_cases(registry.store.path,c['id'])['actions']==[]


@pytest.mark.parametrize('forbidden',['change_spec','replace_primary_metric','activate_model','replace_forecast_authority','finalize_from_filename'])
def test_operator_cannot_mutate_protected_domains(registry,tmp_path,forbidden):
    interrupted(registry,tmp_path)
    before=snapshot(registry)
    with pytest.raises(ValueError):
        action(registry,case_for(registry,'stale_running'),forbidden)
    assert snapshot(registry)==before


def model_without_link(registry,completed):
    artifact=next(a for a in registry.list('artifacts') if a['role']=='candidate')
    model=registry.store.put('models',{'id':'model-one','state':'candidate','artifact':artifact['path'],'artifact_sha256':artifact['sha256'],
                                     'metadata':{'experiment_run_id':completed}})
    return model,artifact


def test_safe_unique_linkage(registry,completed):
    model,artifact=model_without_link(registry,completed)
    c=case_for(registry,'incomplete_candidate_link')
    result=action(registry,c,'linked_existing_candidate')
    assert result['state']=='resolved'
    assert registry.list('model_links')[0]['artifact_id']==artifact['id']


def test_ambiguous_candidate_remains_unresolved(registry,completed):
    model,artifact=model_without_link(registry,completed)
    c=case_for(registry,'incomplete_candidate_link')
    registry.store.update('models',model['id'],artifact='another-location')
    with pytest.raises(ValueError,match='Unique'):
        action(registry,c,'linked_existing_candidate')
    assert list_cases(registry.store.path,c['id'])['state']=='open'


def test_wrong_candidate_ownership(registry,completed):
    model,_=model_without_link(registry,completed)
    action(registry,case_for(registry,'incomplete_candidate_link'),'linked_existing_candidate')
    registry.store.update('models',model['id'],metadata={'experiment_run_id':'wrong-run'})
    assert check(verify_run(registry.store.path,completed),'candidate:')[0]['status']=='mismatch'


def test_confirm_restored_artifact(registry,completed):
    a=next(a for a in registry.list('artifacts') if a['role']=='candidate')
    p=Path(a['path']); content=p.read_bytes(); p.unlink()
    c=case_for(registry,'missing_artifact')
    with pytest.raises(FileNotFoundError):
        action(registry,c,'confirmed_existing_artifact')
    p.write_bytes(content)
    assert action(registry,c,'confirmed_existing_artifact')['state']=='resolved'


def blocked_job(registry):
    from stock_app.jobs.store import JobStore
    jobs=JobStore(registry.store)
    with registry.store.connection() as db:
        jobs.save_run(db,{'id':'op-one','name':'monthly','scheduled_at':NOW,'status':'blocked','outputs':{},'attempt':1})
    return jobs


def test_phase0_blocked_case_and_cancel(registry):
    jobs=blocked_job(registry)
    c=case_for(registry,'blocked_operational_job')
    assert action(registry,c,'operational_cancelled')['state']=='resolved'
    assert jobs.get('op-one')['status']=='cancelled'
    assert jobs.get('op-one')['reconciliation']['case_id']==c['id']


def test_phase0_existing_resolution_can_be_acknowledged(registry):
    jobs=blocked_job(registry)
    c=case_for(registry,'blocked_operational_job')
    jobs.update('op-one',status='succeeded',reconciliation={'note':'Phase 0 operator inspected'})
    assert action(registry,c,'confirmed_operational_resolution')['state']=='resolved'


def test_phase0_cannot_cancel_live_legacy_job(registry):
    jobs=blocked_job(registry)
    registry.store.put('jobs',{'id':'live-job','state':'running'})
    jobs.update('op-one',outputs={'legacy_job_id':'live-job'})
    with pytest.raises(ValueError,match='legacy job'):
        action(registry,case_for(registry,'blocked_operational_job'),'operational_cancelled')


def test_audit_integrates_both_prior_audits(registry,completed):
    before=snapshot(registry)
    result=audit_integrity(registry.store.path)
    assert result['registry']['status']=='ok' and result['forecast_ledger']['status']=='healthy'
    assert result['replay_performed'] is False and snapshot(registry)==before


def test_deep_audit_checks_bytes_not_replay(registry,completed):
    with patch('stock_app.research.integrity_replay._execute',side_effect=AssertionError('no implicit replay')):
        assert audit_integrity(registry.store.path,depth='artifact')['evidence']['verified']>0


def test_cli_evidence(registry,completed,capsys):
    main(['--database',str(registry.store.path),'evidence',completed])
    assert json.loads(capsys.readouterr().out)['manifest']['run']['id']==completed


def test_cli_replay(registry,completed,capsys):
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake):
        main(['--database',str(registry.store.path),'replay',completed,'--request-key','cli'])
    assert json.loads(capsys.readouterr().out)['status']=='exact'


def test_cli_cases(registry,tmp_path,capsys):
    interrupted(registry,tmp_path)
    case_for(registry,'stale_running')
    main(['--database',str(registry.store.path),'reconcile','list'])
    assert json.loads(capsys.readouterr().out)[0]['state']=='open'


def test_explicit_audit_replay(registry,completed,capsys):
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake):
        main(['--database',str(registry.store.path),'audit','--depth','artifact','--replay','--request-key','batch'])
    assert json.loads(capsys.readouterr().out)['reproduction_checks'][0]['status']=='exact'


@pytest.mark.parametrize('endpoint', ['evidence','verification','reproductions'])
def test_readonly_api(registry,completed,monkeypatch,endpoint):
    from flask import Flask
    from stock_app.research.api import api
    monkeypatch.setenv('STOCK_RESEARCH_ROOT',str(registry.store.path.parent))
    app=Flask(__name__); app.register_blueprint(api)
    before=snapshot(registry)
    with patch('stock_app.research.api.runtime',side_effect=AssertionError('no runtime')):
        response=app.test_client().get(f'/api/research/integrity/runs/{completed}/{endpoint}')
    assert response.status_code==200 and snapshot(registry)==before
    assert app.test_client().post(f'/api/research/integrity/runs/{completed}/{endpoint}').status_code==405


def test_concurrent_verification(registry,completed):
    with ThreadPoolExecutor(max_workers=4) as executor:
        reports=list(executor.map(lambda _:verify_run(registry.store.path,completed,'artifact',clock=lambda:NOW),range(4)))
    assert all(r==reports[0] for r in reports)


def test_concurrent_reconciliation(registry,completed):
    model_without_link(registry,completed)
    c=case_for(registry,'incomplete_candidate_link')
    def attempt(_):
        try:
            return action(registry,c,'linked_existing_candidate')['state']
        except ValueError:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(attempt,range(2)))
    assert sorted(results)==['refused','resolved'] and len(registry.list('model_links'))==1


@pytest.mark.parametrize('table',['integrity_reproductions','integrity_results','integrity_cases','integrity_actions'])
def test_append_only_sql_constraints(registry,completed,table):
    with patch('stock_app.research.integrity_replay._execute',side_effect=replay_fake):
        request_reproduction(registry.store,completed,request_key='immutability')
    model_without_link(registry,completed)
    action(registry,case_for(registry,'incomplete_candidate_link'),'linked_existing_candidate')
    for operation in ('UPDATE '+table+' SET created_at=created_at','DELETE FROM '+table):
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            with registry.store.connection() as db:
                db.execute(operation)


@pytest.mark.parametrize('task',['binary','regression'])
def test_real_engine_reproduction(registry,tmp_path,task):
    from stock_app.research.experiments import run_experiment
    run_experiment(history(),registry=registry,execution_key='real-'+task,**options(tmp_path,task))
    run=registry.list('runs')[0]['id']
    before=snapshot(registry)
    result=request_reproduction(registry.store,run,request_key='real-check')
    comparisons=result['result']['document']['comparisons']
    numerical=[c for c in comparisons if c['path'] not in {'candidate.artifact_sha256','artifact.sha256'}]
    assert numerical and all(c['status']=='exact' for c in numerical)
    assert result['status']=='different'  # Existing bundle serialization preserves tuple/list representation.
    assert {c['path'] for c in comparisons if c['status']=='different'}=={'candidate.artifact_sha256','artifact.sha256'}
    assert snapshot(registry)==before


def test_metadata_audit_avoids_artifact_io(registry,completed):
    with patch.object(Path,'read_bytes',side_effect=AssertionError('metadata audit read bytes')):
        result=audit_integrity(registry.store.path)
    assert result['depth']=='metadata'


def test_concurrent_reproduction_same_key(registry,completed):
    import threading
    entered,release=threading.Event(),threading.Event()
    def slow(path,m,output):
        entered.set()
        assert release.wait(timeout=10)
        return replay_fake(path,m,output)
    with patch('stock_app.research.integrity_replay._execute',side_effect=slow):
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(request_reproduction,registry.store,completed,request_key='concurrent')
            assert entered.wait(timeout=10)
            try:
                other=request_reproduction(registry.store,completed,request_key='concurrent')
                assert other['status']=='blocked'
            finally:
                release.set()
            assert first.result()['status']=='exact'
    assert len(reproduction_history(registry.store.path))==1


def test_unique_reproduction_request_and_foreign_keys(registry,completed):
    with registry.store.connection() as db:
        db.execute('INSERT INTO integrity_reproductions VALUES(?,?,?,?,?)',('r1',completed,'key',NOW,'{}'))
    for values in [('r2',completed,'key',NOW,'{}'),('r3','absent','another',NOW,'{}')]:
        with pytest.raises(sqlite3.IntegrityError):
            with registry.store.connection() as db:
                db.execute('INSERT INTO integrity_reproductions VALUES(?,?,?,?,?)',values)


def test_reproduction_detects_concurrent_original_change(registry,completed):
    def changed(path,m,output):
        result=replay_fake(path,m,output)
        a=next(a for a in m['artifacts'] if a['role']=='candidate')
        Path(a['path']).write_bytes(b'changed during verification')
        return result
    with patch('stock_app.research.integrity_replay._execute',side_effect=changed):
        assert request_reproduction(registry.store,completed,request_key='concurrent-change')['status']=='blocked'


def test_missing_snapshot_and_snapshot_checksum(registry,tmp_path):
    from stock_app.research.data import DataRepository
    from stock_app.research.integrity import load_input
    import pandas as pd
    root=registry.store.path.parent/'datasets'
    directory=root/'TEST'/'snapshot-one'; directory.mkdir(parents=True)
    frame=history()
    frame.index=pd.to_datetime(frame.index,utc=True)
    frame['AvailableAt']=frame.index
    data=directory/'adjusted.csv'; frame.to_csv(data,index_label='Session')
    raw=directory/'raw.csv'; raw.write_bytes(b'raw')
    meta={'id':'snapshot-one','status':'valid','rows':len(frame),'path':str(data),'sha256':sha256(data.read_bytes()).hexdigest(),
          'raw_path':str(raw),'raw_sha256':sha256(raw.read_bytes()).hexdigest()}
    (directory/'metadata.json').write_text(json.dumps(meta))
    assert len(load_input(registry.store.path,{'snapshot_id':'snapshot-one'}))==len(frame)
    with pytest.raises(FileNotFoundError):
        load_input(registry.store.path,{'snapshot_id':'missing'})
    raw.write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        load_input(registry.store.path,{'snapshot_id':'snapshot-one'})


def test_snapshot_identity_cannot_resolve_twice(registry,tmp_path):
    from stock_app.research.integrity import load_input
    root=registry.store.path.parent/'datasets'
    for name in ('one','two'):
        d=root/'TEST'/name; d.mkdir(parents=True)
        (d/'metadata.json').write_text('{"id":"duplicate"}')
    with pytest.raises(ValueError,match='ambiguously'):
        load_input(registry.store.path,{'snapshot_id':'duplicate'})


def linked_forecast(registry,completed):
    from stock_app.research.ledger import issue_forecast
    model,artifact=model_without_link(registry,completed)
    action(registry,case_for(registry,'incomplete_candidate_link'),'linked_existing_candidate')
    target=registry.list('specs')[0]['document']['contract']['target']
    metadata={'experiment_run_id':completed,'candidate':{'artifact_version':1,'task':'binary','horizon':target['horizon'],
        'event_threshold':target['event_threshold'],'feature_config':{'interval':'1d'},'active_cutoff':'2026-06-01T00:00:00+00:00'}}
    registry.store.update('models',model['id'],task='binary',symbol='TEST',metadata=metadata)
    return issue_forecast(registry.store,symbol='TEST',model_id=model['id'],snapshot_id='forecast-data',origin='2026-07-02',
                          payload={'probability':.6,'origin_close':100.},issued_at='2026-07-02T21:00:00+00:00',horizon=target['horizon'])


def test_canonical_forecast_provenance(registry,completed):
    forecast=linked_forecast(registry,completed)
    value=verify_run(registry.store.path,completed,'artifact')
    assert check(value,'forecast:')[0]['status']=='verified'
    assert check(value,'forecast.fingerprint:')[0]['status']=='verified'
    assert check(value,'forecast.issuance:')[0]['status']=='verified'
    before=get_evidence(registry.store.path,completed)
    registry.store.update('forecasts',forecast['id'],state='resolved',outcome={'value':.1})
    assert get_evidence(registry.store.path,completed)==before


def test_forecast_future_cutoff_detected(registry,completed):
    linked_forecast(registry,completed)
    model=registry.store.get('models','model-one')
    model['metadata']['candidate']['active_cutoff']='2027-01-01T00:00:00+00:00'
    registry.store.put('models',model)
    assert check(verify_run(registry.store.path,completed),'forecast.cutoff:')[0]['status']=='mismatch'


def test_scheduler_case_links_research_and_operational(registry,tmp_path):
    jobs=blocked_job(registry)
    run_id=interrupted(registry,tmp_path)
    doc=registry.get('runs',run_id)['document']; doc['job_id']='operational-op-one'
    tamper(registry,'runs','UPDATE research_runs SET document=?',(canonical_json(doc),))
    jobs.update('op-one',outputs={'legacy_job_id':'operational-op-one'})
    c=case_for(registry,'blocked_operational_job')
    assert c['document']['related']=={'operational_id':'op-one','run_id':run_id}
    assert registry.state(run_id)=='running'


def test_migration_backup_and_restart_safety(registry,completed):
    from stock_app.research.integrity_migration import migrate
    model_without_link(registry,completed)
    before=snapshot(registry)
    one=migrate(registry.store.path)
    two=migrate(registry.store.path)
    assert Path(one['backup']).is_file() and Path(two['backup']).is_file()
    assert one['before']==one['after']==two['before']==two['after']
    assert snapshot(registry)==before


def test_link_action_failure_rolls_back_link(registry,completed):
    model_without_link(registry,completed)
    c=case_for(registry,'incomplete_candidate_link')
    with registry.store.connection() as db:
        db.execute("CREATE TRIGGER reject_link_action BEFORE INSERT ON integrity_actions BEGIN SELECT RAISE(ABORT,'action failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        action(registry,c,'linked_existing_candidate')
    assert registry.list('model_links')==[]


def test_resolved_case_remains_visible_on_redetection(registry,completed):
    model_without_link(registry,completed)
    c=case_for(registry,'incomplete_candidate_link')
    action(registry,c,'linked_existing_candidate')
    assert any(v['id']==c['id'] and v['state']=='resolved' for v in detect_cases(registry.store,clock=lambda:LATER))


def test_pretty_printed_engine_report_is_not_orphan(registry,completed):
    output=Path(registry.get('runs',completed)['document']['output'])
    file=output/'report.json'
    file.write_text(json.dumps(json.loads(file.read_text()),indent=2))
    assert not any(f['category']=='orphan_artifact' for f in findings(registry.store.path,depth='artifact'))


def test_completed_orphan_report_stays_under_review(registry,tmp_path):
    run=interrupted(registry,tmp_path)
    output=tmp_path/'interrupted'; output.mkdir()
    (output/'report.json').write_text('{"status":"completed"}')
    case=case_for(registry,'stale_running')
    assert case['document']['evidence']['classification']=='complete_report_requires_review'
    assert registry.state(run)=='running'
