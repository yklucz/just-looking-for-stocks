import numpy as np
import pandas as pd
import pytest

from stock_app.prediction import training_jobs as jobs
from stock_app.prediction.schemas import PredictionError


def missing(*args, **kwargs):
    raise PredictionError('MODEL_NOT_AVAILABLE', 'missing')


def test_training_queue_deduplicates_and_is_bounded(monkeypatch):
    monkeypatch.setattr(jobs, '_jobs', {})
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    calls = []
    monkeypatch.setattr(jobs._executor, 'submit', lambda *args: calls.append(args))
    assert jobs.request_training('nvda')['status'] == 'training'
    jobs.request_training('NVDA')
    assert len(calls) == 1
    for ticker in ('AAPL', 'SPY', 'MSFT'):
        jobs.request_training(ticker)
    with pytest.raises(PredictionError, match='queue is full'):
        jobs.request_training('AMD')


def test_available_model_never_retrains(monkeypatch):
    monkeypatch.setattr(jobs, 'load_bound_model', lambda *a: None)
    assert jobs.request_training('NVDA')['status'] == 'ready'


def test_failed_training_returns_error(monkeypatch):
    monkeypatch.setattr(jobs, '_jobs', {})
    monkeypatch.setattr(jobs, 'train_and_bind', lambda ticker: (_ for _ in ()).throw(ValueError('bad candles')))
    jobs._run('NVDA')
    assert jobs._jobs['NVDA'] == {'ticker': 'NVDA', 'status': 'failed', 'error': 'bad candles'}


def test_train_save_bind_and_predict(monkeypatch, tmp_path):
    from time import monotonic, sleep
    from stock_app.app import app
    from stock_app.prediction import service
    rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .01, 700)))
    history = pd.DataFrame({'Open': close, 'High': close * 1.01, 'Low': close * .99,
                            'Close': close, 'Volume': rng.integers(100, 10000, 700)},
                           index=pd.bdate_range('2020-01-01', periods=700))
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'manifest.json'))
    monkeypatch.setattr(jobs, 'load_history', lambda ticker: history)
    monkeypatch.setattr(service, 'load_history', lambda ticker: history)
    monkeypatch.setattr(jobs, '_jobs', {})
    monkeypatch.setattr(jobs, '_finished_at', {})
    client = app.test_client()
    response = client.post('/api/train', json={'ticker': 'NVDA'})
    assert response.status_code == 202
    deadline = monotonic() + 10
    while monotonic() < deadline:
        response = client.post('/api/train', json={'ticker': 'NVDA'})
        if response.get_json()['status'] != 'training':
            break
        sleep(.01)
    assert response.get_json()['status'] == 'ready'
    result = service.predict('NVDA')
    assert 0 <= result['prediction']['probability_up'] <= 1
    assert result['prediction']['horizon'] == 5
    assert jobs.request_training('NVDA')['status'] == 'ready'


def test_training_api_json_contract(monkeypatch):
    from stock_app.app import app
    calls = []
    def request(ticker):
        calls.append(ticker)
        return {'status': 'training', 'ticker': ticker}
    monkeypatch.setattr(jobs, 'request_training', request)
    client = app.test_client()
    response = client.post('/api/train', json={'ticker': 'NVDA'})
    assert response.status_code == 202
    assert response.get_json()['status'] == 'training'
    assert calls == ['NVDA']
    assert client.post('/api/train').status_code == 400
    assert client.post('/api/train', json={}).status_code == 400
    assert client.get('/api/train').status_code in {404, 405}
    assert calls == ['NVDA']  # GET must never enqueue work, even with the static fallback.


def test_training_api_blocks_cross_origin_mutation(monkeypatch):
    from stock_app.app import app
    from stock_app.prediction import training_jobs as training

    calls = []
    monkeypatch.setattr(
        training,
        'request_training',
        lambda ticker: calls.append(ticker) or {'status': 'training', 'ticker': ticker},
    )
    client = app.test_client()

    blocked = client.post(
        '/api/train',
        json={'ticker': 'NVDA'},
        headers={'Origin': 'https://attacker.example'},
    )
    assert blocked.status_code == 403
    assert blocked.get_json()['code'] == 'CSRF_BLOCKED'
    assert calls == []

    allowed = client.post(
        '/api/train',
        json={'ticker': 'NVDA'},
        headers={'Origin': 'http://localhost'},
    )
    assert allowed.status_code == 202
    assert calls == ['NVDA']


def test_incompatible_artifact_is_not_overwritten(monkeypatch):
    def incompatible(*args):
        raise PredictionError('MODEL_INCOMPATIBLE', 'checksum mismatch')
    monkeypatch.setattr(jobs, 'load_bound_model', incompatible)
    with pytest.raises(PredictionError, match='checksum'):
        jobs.request_training('NVDA')


def test_failed_job_can_retry_after_cooldown(monkeypatch):
    monkeypatch.setattr(jobs, '_jobs', {'NVDA': {'status': 'failed', 'ticker': 'NVDA'}})
    monkeypatch.setattr(jobs, '_finished_at', {'NVDA': 0})
    monkeypatch.setattr(jobs, 'load_bound_model', missing)
    monkeypatch.setattr(jobs, 'monotonic', lambda: 61)
    calls = []
    monkeypatch.setattr(jobs._executor, 'submit', lambda *args: calls.append(args))
    assert jobs.request_training('NVDA')['status'] == 'training'
    assert len(calls) == 1
