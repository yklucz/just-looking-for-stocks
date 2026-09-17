"""Dashboard training requests persist candidates without mutating active bindings."""
import pytest
from stock_app.prediction import training_jobs as jobs
from stock_app.prediction.schemas import PredictionError


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from stock_app.research.runtime import ResearchRuntime
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path / 'manifest.json'))
    value = ResearchRuntime(tmp_path / 'research')
    monkeypatch.setattr('stock_app.research.runtime.get_runtime', lambda: value)
    return value


def missing(*args, **kwargs):
    raise PredictionError('MODEL_NOT_AVAILABLE', 'missing')


def test_training_queue_deduplicates(runtime, monkeypatch):
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    first = jobs.request_training('nvda')
    assert first['status'] == 'training'
    assert jobs.request_training('NVDA')['job_id'] == first['job_id']
    assert len(runtime.store.list('jobs')) == 1
    assert runtime.store.list('models') == []


def test_available_model_never_retrains(runtime, monkeypatch):
    monkeypatch.setattr(jobs, 'load_bound_model', lambda *a, **k: None)
    assert jobs.request_training('NVDA')['status'] == 'ready'
    assert runtime.store.list('jobs') == []


def test_candidate_requires_review_and_does_not_bind(runtime, monkeypatch):
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    runtime.store.put('models', {'id': 'new', 'symbol': 'NVDA', 'task': 'binary', 'state': 'candidate'})
    assert jobs.request_training('NVDA')['status'] == 'candidate'
    assert runtime.store.active('NVDA', 'binary') is None


def test_failed_training_requires_explicit_resume(runtime, monkeypatch):
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    first = jobs.request_training('NVDA')
    runtime.store.update('jobs', first['job_id'], state='failed', error='bad candles')
    assert jobs.request_training('NVDA')['error'] == 'bad candles'
    assert len(runtime.store.list('jobs')) == 1
    runtime.control(first['job_id'], 'resume')
    assert jobs.request_training('NVDA')['status'] == 'training'


def test_training_api_json_and_same_origin_contract(runtime, monkeypatch):
    from stock_app.app import app
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    monkeypatch.setitem(app.config, 'RESEARCH_BACKGROUND', False)
    client = app.test_client()
    response = client.post('/api/train', json={'ticker': 'NVDA'})
    assert response.status_code == 202
    assert response.json['job_id']
    assert client.post('/api/train').status_code == 400
    assert client.post('/api/train', json={}).status_code == 400
    assert client.get('/api/train').status_code in {404, 405}
    assert client.post('/api/train', json={'ticker': 'NVDA'}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert len(runtime.store.list('jobs')) == 1


def test_incompatible_artifact_is_not_overwritten(runtime, monkeypatch):
    def incompatible(*args, **kwargs):
        raise PredictionError('MODEL_INCOMPATIBLE', 'checksum mismatch')
    monkeypatch.setattr(jobs, 'load_bound_model', incompatible)
    with pytest.raises(PredictionError, match='checksum'):
        jobs.request_training('NVDA')
    assert runtime.store.list('jobs') == []
