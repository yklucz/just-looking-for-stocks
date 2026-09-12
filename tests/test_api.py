"""Saved-model API and frontend contract; no network or request-time training."""
import ast
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import stock_app.app as api
from stock_app.config import DEFAULT_SPLIT, TargetConfig, XGBoostConfig
from stock_app.models import XGBoostClassifier
from stock_app.models.registry import model_contract, save_model
from stock_app.prediction import service
from stock_app.prediction.artifact_loader import _load
from stock_app.prediction.schemas import PredictionError
from stock_app.training.feature_dataset import build_feature_dataset
from research_data import synthetic_market


@pytest.fixture
def bound(tmp_path, monkeypatch):
    raw = synthetic_market(False, rows=800)
    dataset = build_feature_dataset(raw, target_config=TargetConfig(horizon=5, task='binary'))
    model = XGBoostClassifier(XGBoostConfig(n_estimators=10, early_stopping_rounds=2))
    model.fit(*dataset.partition('train'), validation=dataset.partition('validation'))
    contract = model_contract(model, dataset, 'AAPL', asdict(DEFAULT_SPLIT))
    path = save_model(model, tmp_path/'model', contract, {})
    metadata = json.loads((path/'metadata.json').read_text())
    manifest = tmp_path/'bindings.json'
    manifest.write_text(json.dumps({'bindings': {'AAPL:xgboost': {
        'artifact': str(path), 'fingerprint': metadata['fingerprint'], 'model_sha256': metadata['model_sha256']}}}))
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(manifest))
    monkeypatch.setattr(service, 'load_history', lambda ticker: raw.copy())
    _load.cache_clear()
    yield raw, model, manifest, path
    _load.cache_clear()


def test_modern_prediction_contract_and_no_training(bound, monkeypatch):
    raw, model, _, _ = bound
    def forbidden(*a, **kw):
        raise AssertionError('API attempted training')
    monkeypatch.setattr(XGBoostClassifier, 'fit', forbidden)
    response = api.app.test_client().get('/api/predict?ticker=apple')
    assert response.status_code == 200
    d = response.get_json()
    assert d['schema_version'] == 'prediction-v2' and d['ticker'] == 'AAPL'
    assert d['prediction']['horizon'] == 5 and d['prediction']['event_threshold'] == .002
    assert 0 <= d['prediction']['probability_up'] <= 1
    assert d['model']['type'] == 'xgboost' and len(d['model']['version']) == 64
    assert d['model']['stale'] and d['market']['stale']
    assert d['signal']['action'] in {'LONG','FLAT'} and d['signal']['decision_threshold'] == .5
    expected = model.predict_proba(service.build_features(raw).frame.iloc[[-1]])[0]
    assert d['prediction']['probability_up'] == pytest.approx(float(expected))
    assert not {'predicted_next_price','predicted_next_close','forecast_path','rmse','validation'} & d.keys()
    assert api.app.test_client().get('/api/predict?ticker=AAPL').get_json()['prediction'] == d['prediction']
    assert _load.cache_info().hits >= 1


@pytest.mark.parametrize('query,code', [('ticker=MSFT','MODEL_NOT_AVAILABLE'),('ticker=AAPL&model=gru','UNSUPPORTED_MODEL'),
                                     ('ticker=AAPL&interval=1h','UNSUPPORTED_TASK'),('ticker=AAPL&price_field=open','UNSUPPORTED_TASK')])
def test_prediction_missing_or_unsupported(bound, query, code):
    response = api.app.test_client().get('/api/predict?'+query)
    assert response.status_code in {400,503}
    assert response.get_json()['code'] == code


@pytest.mark.parametrize('mutation', ['feature_order','threshold','horizon','weights','metadata','feature_file'])
def test_artifact_mismatch_rejected(bound, mutation):
    _, _, _, path = bound
    # Populate cache, then prove tampering cannot bypass the next request's checks.
    service.predict('AAPL')
    m = json.loads((path/'metadata.json').read_text())
    if mutation == 'feature_order': m['contract']['feature_names'].reverse()
    elif mutation == 'threshold': m['contract']['target']['threshold'] = .01
    elif mutation == 'horizon': m['contract']['target']['horizon'] = 20
    elif mutation == 'metadata': m['fingerprint'] = 'wrong'
    elif mutation == 'feature_file': (path/'feature_names.json').write_text('[]')
    else:
        with (path/'model.json').open('ab') as handle: handle.write(b'changed')
    (path/'metadata.json').write_text(json.dumps(m))
    r = api.app.test_client().get('/api/predict?ticker=AAPL')
    assert r.status_code == 503 and r.get_json()['code'] == 'MODEL_INCOMPATIBLE'


def test_generated_feature_order_checked(bound, monkeypatch):
    original = service.build_features
    from dataclasses import replace
    def reversed_features(*args):
        result = original(*args)
        return replace(result, all_features=result.all_features.iloc[:,::-1])
    monkeypatch.setattr(service, 'build_features', reversed_features)
    with pytest.raises(PredictionError, match='feature order'):
        service.predict('AAPL')


def test_completed_candles_and_future_outliers(bound, monkeypatch):
    raw, _, _, _ = bound
    now = pd.Timestamp(raw.index[-2], tz='UTC') + pd.Timedelta(hours=12)
    before = service.predict('AAPL', now=now.to_pydatetime())
    changed = raw.copy(); changed.iloc[-2:] *= 100
    monkeypatch.setattr(service, 'load_history', lambda t: changed)
    after = service.predict('AAPL', now=now.to_pydatetime())
    assert before['prediction'] == after['prediction']
    assert before['timestamp'] == raw.index[-3].isoformat()
    # An incomplete current candle may have no valid close yet; it is not input.
    changed.iloc[-2:] = np.nan
    assert service.predict('AAPL', now=now.to_pydatetime())['prediction'] == before['prediction']


def test_prediction_data_failure_and_invalid_context(bound, monkeypatch):
    raw, _, _, _ = bound
    for frame in (raw.iloc[1:], raw.drop(columns='Volume')):
        monkeypatch.setattr(service, 'load_history', lambda t: frame)
        assert api.app.test_client().get('/api/predict?ticker=AAPL').status_code == 503
    def unavailable(ticker): raise ValueError('Yahoo unavailable')
    monkeypatch.setattr(service, 'load_history', unavailable)
    r = api.app.test_client().get('/api/predict?ticker=AAPL')
    assert r.status_code == 503 and r.get_json()['code'] == 'PREDICTION_UNAVAILABLE'


def test_prediction_ignores_invalid_trailing_candle(bound, monkeypatch):
    raw, _, _, _ = bound
    changed = raw.copy()
    changed.iloc[-1, changed.columns.get_loc('Close')] = changed.iloc[-1]['High'] * 1.01
    monkeypatch.setattr(service, 'load_history', lambda t: changed)
    response = api.app.test_client().get('/api/predict?ticker=AAPL')
    assert response.status_code == 200
    assert response.get_json()['timestamp'] == raw.index[-2].isoformat()


def test_existing_routes_and_static_files(bound, monkeypatch):
    raw, _, _, _ = bound
    market = raw.reset_index(names='Date'); market.Date = market.Date.dt.strftime('%Y-%m-%d')
    monkeypatch.setattr(api, 'get_history', lambda *a, **kw: (market.copy(), '1d'))
    monkeypatch.setattr(api, 'get_stock_info', lambda ticker: {'symbol': ticker})
    monkeypatch.setattr(api, 'search_symbols', lambda query: [{'symbol':'AAPL','name':'Apple'}])
    monkeypatch.setattr(api, 'list_symbols', lambda: [{'symbol':'AAPL','name':'Apple'}])
    client = api.app.test_client()
    for url in ['/', '/script.js', '/style.css', '/api/history?ticker=apple','/api/info?ticker=apple','/api/search?q=apple','/api/symbols']:
        assert client.get(url).status_code == 200
    assert len(client.get('/api/history?ticker=AAPL').get_json()['prices']) == len(market)
    for url in ['/api/info','/api/history','/api/predict']: assert client.get(url).status_code == 400


def test_history_replaces_nonfinite_values_with_json_null(bound, monkeypatch):
    raw, _, _, _ = bound
    market = raw.reset_index(names='Date')
    market.Date = market.Date.dt.strftime('%Y-%m-%d')
    market.loc[0, 'Close'] = np.nan
    monkeypatch.setattr(api, 'get_history', lambda *a, **kw: (market.copy(), '1d'))
    response = api.app.test_client().get('/api/history?ticker=AAPL')
    assert response.status_code == 200
    assert response.get_json()['prices'][0]['Close'] is None


def test_no_tensorflow_runtime_imports_or_installation():
    for root in ('stock_app','scripts'):
        for path in Path(root).rglob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                assert not any(n.split('.')[0] in {'tensorflow','keras'} for n in names), path
    assert importlib.util.find_spec('tensorflow') is None
    assert importlib.util.find_spec('keras') is None
    assert not Path('stock_app/training/legacy_model.py').exists()


def test_frontend_has_modern_contract_only():
    js = Path('stock_app/stocks_dashboard/script.js').read_text()
    html = Path('stock_app/stocks_dashboard/index.html').read_text()
    for old in ('forecast_path','rmse','predicted_next_price','predicted_next_close','predChart','predTable'):
        assert old not in js and old not in html
    for field in ('probability_up','event_threshold','trained_until'): assert field in js
    for element in ('priceChart','historyTable','predictionStatus','predictionValue'): assert element in html
