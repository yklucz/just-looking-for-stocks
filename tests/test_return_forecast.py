import json
import numpy as np
import pandas as pd
import pytest
from dataclasses import asdict
from stock_app.config import DEFAULT_SPLIT, TargetConfig, XGBoostConfig
from stock_app.models.return_model import XGBoostReturnRegressor
from stock_app.models.registry import model_contract, save_model, load_model
from stock_app.training.feature_dataset import build_feature_dataset
from stock_app.prediction import service
from stock_app.prediction.chart import prediction_chart
from research_data import synthetic_market



def bind_fixture(root, raw, ticker, task='binary'):
    from stock_app.models import XGBoostClassifier
    from stock_app.training.model_inputs import fit_partitioned
    dataset = build_feature_dataset(raw, target_config=TargetConfig(horizon=5, task=task))
    config = XGBoostConfig(n_estimators=10, early_stopping_rounds=2)
    model = XGBoostClassifier(config) if task == 'binary' else XGBoostReturnRegressor(config)
    fit_partitioned(model, dataset)
    contract = model_contract(model, dataset, ticker, asdict(DEFAULT_SPLIT))
    path = save_model(model, root / task, contract, {})
    metadata = json.loads((path / 'metadata.json').read_text())
    manifest = root / 'manifest.json'
    value = json.loads(manifest.read_text()) if manifest.exists() else {'version': 1, 'bindings': {}}
    value['bindings'][f'{ticker}:{model.model_type}'] = {
        'artifact': str(path), 'fingerprint': metadata['fingerprint'], 'model_sha256': metadata['model_sha256']}
    manifest.write_text(json.dumps(value))


def test_regression_alignment_save_reload_and_feature_order(tmp_path):
    raw = synthetic_market(False, rows=800)
    ds = build_feature_dataset(raw, target_config=TargetConfig(horizon=5, task='regression'))
    model = XGBoostReturnRegressor(XGBoostConfig(n_estimators=10, early_stopping_rounds=2))
    X, y = ds.partition('train')
    assert y.iloc[0] == pytest.approx(np.log(raw.Close.loc[ds.target_times.loc[X.index[0]]] / raw.Close.loc[X.index[0]]))
    model.fit(X, y, validation=ds.partition('validation'))
    contract = model_contract(model, ds, 'TEST', asdict(DEFAULT_SPLIT))
    path = save_model(model, tmp_path/'regressor', contract, {})
    restored = load_model(path, contract)
    test, _ = ds.partition('test')
    np.testing.assert_array_equal(model.predict(test), restored.predict(test))
    with pytest.raises(ValueError, match='feature'):
        restored.predict(test.iloc[:, ::-1])
    with pytest.raises(ValueError):
        load_model(path, {**contract, 'target': {**contract['target'], 'horizon': 1}})


def test_frozen_chart_probabilities_and_independent_price_regression(monkeypatch, tmp_path):
    raw = synthetic_market(False, rows=800)
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'manifest.json'))
    monkeypatch.setattr(service, 'load_history', lambda ticker: raw.copy())
    bind_fixture(tmp_path, raw, 'TEST')
    before = (tmp_path/'manifest.json').read_text()
    initial = prediction_chart('TEST')
    assert initial['forecast_status'] == 'missing'
    bind_fixture(tmp_path, raw, 'TEST', 'regression')
    after = json.loads((tmp_path/'manifest.json').read_text())
    assert json.loads(before)['bindings']['TEST:xgboost'] == after['bindings']['TEST:xgboost']
    result = prediction_chart('TEST')
    assert result['probabilities'] == initial['probabilities']
    assert result['evaluation']['classification']['samples'] > 0
    assert result['evaluation']['regression']['samples'] > 0
    assert result['evaluation']['regression']['baseline']['name'] == 'Unchanged Close (zero return)'
    forecast = result['forecast']
    assert forecast['estimated_price'] == pytest.approx(forecast['origin_close'] * np.exp(forecast['predicted_log_return']))
    assert forecast['horizon'] == 5 and forecast['origin'] == raw.index[-1].isoformat()
    from stock_app.prediction.artifact_loader import load_bound_model
    _, contract, _ = load_bound_model('TEST', 'xgboost')
    boundary = pd.Timestamp(contract['fit_dates']['validation']['label_end'])
    assert all(pd.Timestamp(p['timestamp']) > boundary for p in result['probabilities'])
    # Extending future history cannot change probabilities already shown.
    monkeypatch.setattr(service, 'load_history', lambda ticker: raw.iloc[:-10].copy())
    prefix = prediction_chart('TEST')['probabilities']
    assert prefix == result['probabilities'][:-10]


def test_prediction_chart_api_uses_frozen_predictions(monkeypatch, tmp_path):
    from stock_app import app as app_module
    raw = synthetic_market(False, rows=800)
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'manifest.json'))
    monkeypatch.setattr(service, 'load_history', lambda ticker: raw.copy())
    bind_fixture(tmp_path, raw, 'AAPL')
    assert app_module.app.test_client().get('/api/prediction-chart').status_code == 400
    response = app_module.app.test_client().get('/api/prediction-chart?ticker=AAPL')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['ticker'] == 'AAPL'
    assert payload['forecast_status'] == 'missing'
    assert payload['probabilities']


def test_regression_failure_keeps_valid_probabilities(monkeypatch, tmp_path):
    from stock_app.prediction import chart
    raw = synthetic_market(False, rows=800)
    monkeypatch.setenv('STOCK_MODEL_MANIFEST', str(tmp_path/'manifest.json'))
    monkeypatch.setattr(service, 'load_history', lambda ticker: raw.copy())
    bind_fixture(tmp_path, raw, 'TEST')
    bind_fixture(tmp_path, raw, 'TEST', 'regression')
    loader = chart.load_bound_model

    class BrokenRegressor:
        def predict(self, inputs):
            raise ValueError('Invalid regression output')

    def load(ticker, model, **kwargs):
        candidate, contract, version = loader(ticker, model, **kwargs)
        return (BrokenRegressor() if model == 'xgboost_regressor' else candidate), contract, version

    monkeypatch.setattr(chart, 'load_bound_model', load)
    result = prediction_chart('TEST')
    assert result['probabilities']
    assert result['forecast'] is None
    assert result['forecast_status'] == 'unavailable'
    assert result['forecast_error'] == 'Invalid regression output'
