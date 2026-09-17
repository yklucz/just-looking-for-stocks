from stock_app.research.runtime import ResearchRuntime


def test_schedule_uses_completed_exchange_session_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    runtime = ResearchRuntime(tmp_path/'research')
    # Thanksgiving Friday: early close at 18:00 UTC, plus 30-minute delay.
    runtime.schedule('2026-11-27T18:15:00Z')
    with runtime.store.connection() as db:
        keys = [row[0] for row in db.execute('SELECT key FROM job_keys')]
    assert 'refresh:AAPL:2026-11-25' in keys
    runtime.schedule('2026-11-27T18:35:00Z')
    count=len(runtime.store.list('jobs'))
    restarted=ResearchRuntime(tmp_path/'research')
    restarted.schedule('2026-11-28T20:00:00Z')
    assert len(restarted.store.list('jobs')) == count
    with runtime.store.connection() as db:
        assert db.execute("SELECT count(*) FROM job_keys WHERE key='refresh:AAPL:2026-11-27'").fetchone()[0] == 1


def test_worker_lease_excludes_another_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    first=ResearchRuntime(tmp_path/'research'); second=ResearchRuntime(tmp_path/'research')
    with first.worker_lease() as owned:
        assert owned
        with second.worker_lease() as other:
            assert not other
    with second.worker_lease() as other:
        assert other


def test_monthly_training_keeps_frozen_shadow_nomination(tmp_path, monkeypatch):
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    runtime=ResearchRuntime(tmp_path/'research')
    runtime.store.put('models', {'id':'frozen','symbol':'AAPL','task':'binary','state':'shadow',
                                 'nominated_at':'2026-10-01T00:00:00Z'})
    runtime.schedule('2026-11-27T20:00:00Z')
    jobs=[j for j in runtime.store.list('jobs',symbol='AAPL') if j['kind']=='experiment']
    assert {j['parameters']['task'] for j in jobs} == {'binary','regression'}
    assert runtime.store.get('models','frozen')['nominated_at']=='2026-10-01T00:00:00Z'
    assert runtime.store.get('models','frozen')['state']=='shadow'


def test_company_context_job_persists_view_only_records(tmp_path, monkeypatch):
    from stock_app.research import events
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    row={'id':'observed-event','symbol':'AAPL','kind':'events','verified':False,'training_eligible':False}
    monkeypatch.setattr(events,'download_free_context',lambda symbol: {
        'status':'partial','events':[row],'fundamentals':[], 'errors':[{'section':'cash_flow','error':'unavailable'}]})
    runtime=ResearchRuntime(tmp_path/'research')
    job=runtime.submit('company_context','AAPL')
    result=runtime.run_job(job['id'])
    assert result['state']=='completed'
    assert result['progress']['status']=='partial'
    assert runtime.store.get('events','observed-event')['training_eligible'] is False


def test_resumed_completed_fit_preserves_frozen_model_state(tmp_path, monkeypatch):
    import hashlib
    from stock_app.research import experiments
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'absent.json'))
    runtime=ResearchRuntime(tmp_path/'research')
    artifact=tmp_path/'candidate.joblib'
    artifact.write_bytes(b'completed-fit')
    identity=hashlib.sha256(artifact.read_bytes()).hexdigest()
    runtime.store.put('models',{'id':identity,'symbol':'AAPL','task':'binary','state':'shadow',
                               'artifact':str(artifact),'nominated_at':'2026-09-01T21:00:00Z'})
    # Replace only expensive data acquisition/fitting. Registration and recovery use real SQLite.
    monkeypatch.setattr(runtime,'inputs',lambda symbol,snapshots: (None,{}, {'AAPL':'pinned'}))
    monkeypatch.setattr(experiments,'run_experiment',lambda *args,**kwargs: {
        'status':'completed','candidate':{'path':str(artifact)}})
    job=runtime.submit('experiment','AAPL')
    runtime.store.update('jobs',job['id'],state='running')
    runtime.store.recover_jobs()
    assert runtime.run_job(job['id'])['state']=='completed'
    restored=runtime.store.get('models',identity)
    assert restored['state']=='shadow'
    assert restored['nominated_at']=='2026-09-01T21:00:00Z'
