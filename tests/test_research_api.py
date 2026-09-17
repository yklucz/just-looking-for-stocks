import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    from stock_app.app import app
    monkeypatch.setenv('STOCK_RESEARCH_ROOT', str(tmp_path / 'research'))
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path / 'missing.json'))
    app.config.update(TESTING=True, RESEARCH_BACKGROUND=False)
    return app.test_client()


def test_research_lists_and_persistent_job_control(client):
    assert client.get('/research').status_code == 200
    for kind in ('datasets', 'jobs', 'models', 'forecasts'):
        response = client.get('/api/research/' + kind)
        assert response.status_code == 200
        assert response.json == {'items': []}
    response = client.post('/api/research/jobs', json={'kind': 'experiment', 'symbol': 'AAPL', 'task': 'binary'})
    assert response.status_code == 202
    identity = response.json['id']
    assert client.get('/api/research/jobs').json['items'][0]['id'] == identity
    assert client.post(f'/api/research/jobs/{identity}/cancel').json['state'] == 'cancelled'
    assert client.post(f'/api/research/jobs/{identity}/resume').json['state'] == 'queued'


def test_mutations_reject_cross_origin_invalid_requests_and_unknown_models(client):
    assert client.post('/api/research/jobs', json={'kind': 'refresh', 'symbol': 'AAPL'},
                       headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/research/jobs', json={'kind': 'erase', 'symbol': 'AAPL'}).status_code == 400
    assert client.post('/api/research/jobs', json={'kind': 'refresh', 'symbol': '../AAPL'}).status_code == 400
    assert client.post('/api/research/models/unknown/activate').status_code == 404


def test_missing_dashboard_model_creates_candidate_job_without_binding(client):
    response = client.post('/api/train', json={'ticker': 'AAPL'})
    assert response.status_code == 202
    assert response.json['job_id']
    assert response.json['status'] == 'training'
    assert client.get('/api/research/models').json['items'] == []


def test_csv_export_handles_nested_data_and_formulas(client):
    from stock_app.research.runtime import get_runtime
    runtime = get_runtime()
    runtime.store.put('datasets', {'id': 'x', 'symbol': '=danger', 'status': 'valid', 'details': {'rows': 2}})
    response = client.get('/api/research/datasets?format=csv')
    assert response.status_code == 200
    assert response.mimetype == 'text/csv'
    assert "'=danger" in response.text


def test_issued_legacy_prediction_pins_inputs_and_is_idempotent(client):
    import pandas as pd
    from stock_app.research.runtime import get_runtime
    from stock_app.research.inference import record_legacy_inference
    runtime = get_runtime()
    version = 'f' * 64
    runtime.store.put('models', {'id': 'legacy-' + version[:32], 'symbol': 'AAPL', 'task': 'binary', 'state': 'active'})
    history = pd.DataFrame({'Close': [100., 101.]}, index=pd.to_datetime(['2026-11-25','2026-11-27'], utc=True))
    response = {'generated_at': '2026-11-27T18:30:00Z', 'timestamp': '2026-11-27T00:00:00Z',
                'prediction': {'probability_up': .6}, 'model': {'version': version}}
    one = record_legacy_inference('AAPL', version, history, response)
    two = record_legacy_inference('AAPL', version, history, response)
    assert one['id'] == two['id']
    assert one['kind'] == 'prospective'
    assert len(runtime.store.list('datasets')) == 1
    assert len(runtime.store.list('forecasts')) == 1


def test_experiment_period_filter_uses_held_out_origins(client):
    from stock_app.research.runtime import get_runtime
    store = get_runtime().store
    for identity, date in [('old','2024-01-10'), ('match','2025-06-20')]:
        store.put('jobs', {'id':identity, 'symbol':'AAPL', 'kind':'experiment', 'state':'completed',
                          'progress':{'folds':[{'boundaries':{'test':{'origin_start':date, 'origin_end':date}}}]}})
    response = client.get('/api/research/jobs?from=2025-06-20&to=2025-06-20')
    assert [row['id'] for row in response.json['items']] == ['match']
    assert client.get('/api/research/jobs?from=not-a-date').status_code == 400
    assert client.get('/api/research/jobs?from=2026-01-01&to=2025-01-01').status_code == 400


@pytest.mark.parametrize('path,body', [
    ('jobs', {'kind': [], 'symbol': 'AAPL'}),
    ('jobs', {'kind': 'experiment', 'symbol': 'AAPL', 'task': []}),
    ('jobs', {'kind': 'refresh', 'symbol': None}),
    ('settings', {'symbols': [{}]}),
    ('events', {'kind':'events','records': {'symbol':['AAPL'], 'event_date':['2024-01-01'],
                                         'available_at':['2024-01-01T21:00Z']}}),
])
def test_malformed_research_requests_return_400_without_writes(client, path, body):
    from stock_app.app import app
    from stock_app.research.runtime import get_runtime
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.post('/api/research/' + path, json=body)
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 400
    assert response.json['code'] == 'INVALID_REQUEST'
    for kind in ['jobs','events']:
        assert get_runtime().store.list(kind) == []


@pytest.mark.parametrize('artifact_state', ['missing', 'missing_metadata'])
def test_unavailable_active_artifact_returns_service_error(client, tmp_path, monkeypatch, artifact_state):
    import hashlib
    import pandas as pd
    from stock_app.app import app
    from stock_app.research.runtime import get_runtime
    runtime = get_runtime()
    artifact = tmp_path / 'candidate.bin'
    content = b'fixture artifact whose metadata is unavailable'
    if artifact_state == 'missing_metadata':
        artifact.write_bytes(content)
    runtime.store.put('models', {'id':'broken', 'symbol':'AAPL', 'task':'binary', 'state':'candidate',
                                'artifact':str(artifact), 'artifact_sha256':hashlib.sha256(content).hexdigest()})
    runtime.store.set_active('broken')
    # Avoid provider traffic; real artifact verification and HTTP handling still run.
    history = pd.DataFrame({'Close':[100.]}, index=pd.to_datetime(['2026-09-15'], utc=True))
    monkeypatch.setattr(runtime, 'inputs', lambda symbol: (history, {}, {'AAPL':'snapshot'}))
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.get('/api/predict?ticker=AAPL')
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 503
    assert response.json['code'] == 'PREDICTION_UNAVAILABLE'
    assert runtime.store.list('forecasts') == []
    assert runtime.store.active('AAPL', 'binary')['id'] == 'broken'


def test_invalid_event_id_rejects_entire_import(client):
    from stock_app.app import app
    from stock_app.research.runtime import get_runtime
    records = [{'id':identity, 'symbol':'AAPL', 'event_date':'2024-01-01',
                'available_at':'2024-01-01T21:00:00Z'} for identity in ['valid', ['invalid']]]
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.post('/api/research/events', json={'records':records})
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 400
    assert get_runtime().store.list('events') == []


def test_event_import_rolls_back_when_database_rejects_later_row(client):
    from stock_app.app import app
    from stock_app.research.runtime import get_runtime
    store = get_runtime().store
    store.put('events', {'id':'existing', 'symbol':'AAPL', 'source':'original'})
    # A real database failure after an earlier update must roll back that update too.
    with store.connection() as db:
        db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON records "
                   "WHEN NEW.kind='events' AND NEW.id='rejected' "
                   "BEGIN SELECT RAISE(ABORT, 'fixture storage failure'); END")
    records = [{'id':identity, 'symbol':'AAPL', 'event_date':'2024-01-01',
                'available_at':'2024-01-01T21:00:00Z', 'source':'replacement'}
               for identity in ['existing', 'rejected']]
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.post('/api/research/events', json={'records':records})
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 500
    assert store.get('events', 'existing')['source'] == 'original'
    assert len(store.list('events')) == 1


def test_event_import_assigns_ids_to_rows_without_optional_id(client):
    from stock_app.research.runtime import get_runtime
    base = {'symbol':'AAPL', 'event_date':'2024-01-01', 'available_at':'2024-01-01T21:00:00Z'}
    from stock_app.app import app
    previous_propagation = app.config.get('PROPAGATE_EXCEPTIONS')
    app.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        response = client.post('/api/research/events', json={'records':[dict(base, id='provided'), base]})
    finally:
        app.config['PROPAGATE_EXCEPTIONS'] = previous_propagation
    assert response.status_code == 201
    ids = [row['id'] for row in response.json['items']]
    assert ids[0] == 'provided'
    assert isinstance(ids[1], str) and ids[1] and ids[1] != ids[0]
    assert get_runtime().store.get('events', ids[1])['symbol'] == 'AAPL'
