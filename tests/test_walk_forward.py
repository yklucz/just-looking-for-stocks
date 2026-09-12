from dataclasses import asdict, replace
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from xgboost import XGBClassifier

from stock_app.config import TargetConfig, WalkForwardConfig, XGBoostConfig
from stock_app.models import LogisticClassifier, PriorClassifier, XGBoostClassifier
from stock_app.models.metrics import classification_metrics
from stock_app.models.registry import load_model
from stock_app.training.feature_dataset import build_feature_dataset
from stock_app.training.fold_results import distribution
from stock_app.training.folds import dataset_for_fold, generate_folds
from stock_app.training.walk_forward import run_walk_forward
from stock_app.training.walk_forward_report import verify_artifacts
from research_data import synthetic_market


SMALL = WalkForwardConfig(minimum_train_samples=400, validation_samples=100, test_samples=100,
                          step_samples=100, rolling_train_samples=400, max_folds=3)
TARGET = TargetConfig(horizon=5, task="binary")


def reports(summary):
    root = Path(summary['artifact_path'])
    folds = json.loads((root/'folds.json').read_text())
    oof = pd.read_csv(root/'oof_predictions.csv', index_col='timestamp')
    oof.index = pd.to_datetime(oof.index, utc=True)
    return root, folds, oof


@pytest.mark.parametrize("mode", ['expanding', 'rolling'])
@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_fold_generation_and_purging(mode, horizon):
    index = pd.bdate_range('2000-01-01', periods=1000)
    config = replace(SMALL, mode=mode)
    folds = generate_folds(index, horizon, config)
    assert len(folds) == 3
    for number, fold in enumerate(folds):
        assert fold.train_end == 400 + number*100
        assert fold.train_start == (0 if mode == 'expanding' else number*100)
        assert fold.split.train[-1] + horizon < fold.split.validation[0]
        assert fold.split.validation[-1] + horizon < fold.split.test[0]
        assert fold.split.test[-1] + horizon < len(index)
        assert len(fold.split.test) == 100
        assert not np.intersect1d(fold.split.train, fold.split.test).size
        assert not np.intersect1d(fold.split.train, fold.split.validation).size
    np.testing.assert_array_equal(np.concatenate([f.split.test for f in folds]), np.arange(500, 800))
    repeated = generate_folds(index, horizon, config)
    for a, b in zip(folds, repeated):
        np.testing.assert_array_equal(a.split.train, b.split.train)


@pytest.mark.parametrize('kwargs', [{'step_samples': 10}, {'max_folds': 0}, {'mode': 'random'},
                                    {'validation_samples': 0}, {'auc_std_warning': float('nan')}])
def test_invalid_walk_forward_config(kwargs):
    with pytest.raises(ValueError):
        WalkForwardConfig(**kwargs)


def test_insufficient_history_and_horizon():
    index = pd.bdate_range('2000-01-01', periods=200)
    with pytest.raises(ValueError, match='Insufficient'):
        generate_folds(index, 5)
    with pytest.raises(ValueError, match='exceed'):
        generate_folds(index, 100, SMALL)


def test_oof_uniqueness_pooled_metrics_and_snapshot(tmp_path):
    raw = synthetic_market(False, rows=1000)
    result = run_walk_forward(raw, 'TEST', tmp_path, SMALL, target_config=TARGET)
    root, folds, oof = reports(result)
    assert len(oof) == 300 and oof.index.is_unique and oof.index.is_monotonic_increasing
    assert (pd.to_datetime(oof.model_available_after, utc=True) < oof.index).all()
    assert (pd.to_datetime(oof.target_time, utc=True) > oof.index).all()
    np.testing.assert_array_equal(oof.raw_position, np.arange(500, 800))
    np.testing.assert_allclose(oof.future_log_return, np.log(raw.Close.iloc[505:805].to_numpy()/raw.Close.iloc[500:800].to_numpy()))
    expected = classification_metrics(oof.actual_target, oof.xgboost_probability, future_returns=oof.future_log_return)
    assert result['models']['xgboost']['oof']['roc_auc'] == pytest.approx(expected['roc_auc'])
    assert result['models']['xgboost']['aggregate']['roc_auc']['count'] == 3
    assert set(folds[0]['models']['xgboost']['selection']) == {'best_iteration','number_of_trees','grown_trees','validation_log_loss'}
    assert len(pd.read_csv(root/'market_history.csv')) == len(raw)
    assert verify_artifacts(root)['oof_rows'] == 300
    (root/'oof_predictions.csv').write_text('corrupt')
    with pytest.raises(ValueError, match='checksum'):
        verify_artifacts(root)


def test_fold_scalers_fresh_and_train_only(tmp_path):
    raw = synthetic_market(False, rows=1000)
    result = run_walk_forward(raw, 'TEST', tmp_path, SMALL, target_config=TARGET,
                              model_factories={'logistic': LogisticClassifier})
    root, saved, _ = reports(result)
    definitions = generate_folds(raw.index, 5, SMALL)
    data = build_feature_dataset(raw, target_config=TARGET, explicit_split=definitions[0].split)
    scalers = []
    for definition, fold in zip(definitions, saved):
        path = root/fold['models']['logistic']['artifact']
        contract = json.loads((path/'metadata.json').read_text())['contract']
        model = load_model(path, contract)
        scaler = model.estimator.named_steps['standardscaler']
        X, _ = dataset_for_fold(data, definition).partition('train')
        assert scaler.n_samples_seen_ == len(X)
        np.testing.assert_allclose(scaler.mean_, X.mean())
        scalers.append(scaler)
    assert len({id(s) for s in scalers}) == 3
    assert scalers[0].n_samples_seen_ < scalers[1].n_samples_seen_
    assert not np.array_equal(scalers[0].mean_, scalers[1].mean_)


def test_xgboost_instances_fresh_validation_only(tmp_path, monkeypatch):
    calls = []
    original = XGBClassifier.fit
    def record(self, X, y, **kwargs):
        calls.append((self, X.index.copy(), kwargs['eval_set'][0][0].index.copy()))
        return original(self, X, y, **kwargs)
    monkeypatch.setattr(XGBClassifier, 'fit', record)
    raw = synthetic_market(False, rows=1000)
    result = run_walk_forward(raw, 'TEST', tmp_path, SMALL, target_config=TARGET,
                              model_factories={'xgboost': lambda: XGBoostClassifier(XGBoostConfig(n_estimators=10, early_stopping_rounds=2))})
    _, folds, _ = reports(result)
    assert len(calls) == 3 and len({id(c[0]) for c in calls}) == 3
    for (_, train, val), fold in zip(calls, folds):
        test_start = pd.Timestamp(fold['partitions']['test']['origin_start'])
        assert train[-1] < val[0] and val[-1] < test_start
        assert len(val) == 95
        assert pd.Timestamp(fold['partitions']['validation']['label_end']) < test_start


def test_reused_model_factory_is_rejected(tmp_path):
    model = PriorClassifier()
    with pytest.raises(ValueError, match='fresh'):
        run_walk_forward(synthetic_market(False, rows=1000), 'TEST', tmp_path, SMALL,
                         target_config=TARGET, model_factories={'training_prior': lambda: model})


def test_future_outliers_do_not_change_earlier_folds(tmp_path):
    raw = synthetic_market(False, rows=1000)
    first = run_walk_forward(raw, 'TEST', tmp_path, SMALL, target_config=TARGET)
    changed = raw.copy()
    changed.loc[changed.index[750:], ['Open','High','Low','Close']] *= 100
    changed.loc[changed.index[750:], 'Volume'] *= 1000
    second = run_walk_forward(changed, 'TEST', tmp_path, SMALL, target_config=TARGET)
    _, a, pa = reports(first)
    _, b, pb = reports(second)
    assert_frame_equal(pa.iloc[:200], pb.iloc[:200])
    for fold_a, fold_b in zip(a, b):
        # Even fold 2 fitting/selection ends before the outliers at origin 750.
        for model in fold_a['models']:
            assert fold_a['models'][model]['fingerprint'] == fold_b['models'][model]['fingerprint']


@pytest.mark.parametrize('signal', [True, False])
@pytest.mark.parametrize('mode', ['expanding','rolling'])
def test_walk_forward_synthetic_signal_and_noise(tmp_path, signal, mode):
    result = run_walk_forward(synthetic_market(signal), 'SYNTHETIC', tmp_path,
                              WalkForwardConfig(mode=mode, max_folds=5),
                              target_config=TargetConfig(task='binary'), source=f'synthetic seed=17 signal={signal}')
    values = {name: {'pooled_auc': row['oof']['roc_auc'], 'mean_auc': row['aggregate']['roc_auc']['mean']}
              for name, row in result['models'].items()}
    print('WF_SYNTHETIC', json.dumps({'mode':mode, 'signal':signal, 'models':values}))
    for name in ('logistic','xgboost'):
        row = result['models'][name]
        if signal:
            assert row['aggregate']['roc_auc']['min'] > .9
            assert row['oof']['roc_auc'] > .9
        else:
            assert .35 < row['oof']['roc_auc'] < .65


def test_distribution_known_values_and_undefined_metrics():
    stats = distribution([.4, .5, .6, None])
    assert stats['mean'] == .5 and stats['median'] == .5
    assert stats['std'] == pytest.approx(np.std([.4,.5,.6]))
    assert stats['count'] == 3
    assert distribution([None])['mean'] is None


def test_explicit_split_cannot_bypass_purging():
    raw = synthetic_market(False, rows=1000)
    split = generate_folds(raw.index, 5, SMALL)[0].split
    for origins in (np.arange(400), np.array([300, 200], dtype=np.uint64)):
        bad = replace(split, train=origins)
        with pytest.raises(ValueError, match='unpurged'):
            build_feature_dataset(raw, target_config=TARGET, explicit_split=bad)


def test_rolling_features_keep_context_and_matched_ablation(tmp_path):
    from stock_app.config import FeatureConfig
    raw = synthetic_market(False, rows=1000)
    config = replace(SMALL, mode='rolling')
    folds = generate_folds(raw.index, 5, config)
    full = build_feature_dataset(raw, target_config=TARGET, explicit_split=folds[0].split)
    short_config = replace(FeatureConfig(), sma_windows=(5, 10, 20, 50))
    short = build_feature_dataset(raw, short_config, TARGET, explicit_split=folds[0].split)
    assert short.features.warmup_rows == 60
    assert full.features.warmup_rows == 199
    common = full.X.index.intersection(short.X.index)
    for fold in folds:
        a, b = dataset_for_fold(full, fold, common), dataset_for_fold(short, fold, common)
        for name in ('train','validation','test'):
            assert a.partition(name)[0].index.equals(b.partition(name)[0].index)
        train, _ = a.partition('train')
        first = raw.index.get_loc(train.index[0])
        assert train.sma_200.iloc[0] == pytest.approx(raw.Close.iloc[first-199:first+1].mean())


def test_research_batch_falls_back_on_invalid_spy(tmp_path, monkeypatch, capsys):
    from scripts import stage4_research as batch
    import sys
    calls = []
    good = synthetic_market(False, rows=1000)
    def fetch(ticker):
        calls.append(ticker)
        if ticker == 'SPY':
            bad = good.copy()
            bad.loc[bad.index[5], 'High'] = 1
            return bad
        return good
    monkeypatch.setattr(batch, '_get_full_history', fetch)
    monkeypatch.setattr(batch, 'run_walk_forward', lambda raw, ticker, *args, **kwargs: {'artifact_path': ticker})
    monkeypatch.setattr(sys, 'argv', ['stage4_research', '--output', str(tmp_path)])
    batch.main()
    report = json.loads(capsys.readouterr().out)
    assert calls == ['AAPL','SPY','MSFT']
    assert 'SPY' in report['retrieval_failures']
    assert 'MSFT_expanding' in report['runs']
    assert len(list(tmp_path.glob('stage4_rejected_SPY_*.csv'))) == 1
