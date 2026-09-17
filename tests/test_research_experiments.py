"""Temporal safeguards and a small real-estimator end-to-end experiment."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from stock_app.research.experiments import ExperimentConfig, outer_splits, predict_candidate, run_experiment
from stock_app.research.statistics import moving_block_bootstrap, equal_weight_aggregate


def history(n=420):
    rng = np.random.default_rng(17)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    return pd.DataFrame({'Open': close, 'High': close * 1.01, 'Low': close * .99,
                         'Close': close, 'Volume': rng.integers(1000, 5000, n)},
                        index=pd.bdate_range('2020-01-01', periods=n))


def small_config():
    return ExperimentConfig(outer_blocks=2, block_size=30, validation_size=30,
                            calibration_size=30, windows=(160, 'expanding'),
                            settings=({'max_depth': 2, 'learning_rate': .05,
                                       'min_child_weight': 3, 'reg_lambda': 1},),
                            n_estimators=8, early_stopping_rounds=3, bootstrap_resamples=30)


def test_raw_origin_purge_every_boundary():
    for fold in outer_splits(420, small_config()):
        for left, right in [('train', 'validation'), ('validation', 'calibration'),
                            ('calibration', 'test')]:
            assert max(fold[left]) + 5 < min(fold[right])
        assert max(fold['test']) + 5 < fold['test_end']


def test_bootstrap_is_paired_deterministic_and_aggregate_equal_weights():
    candidate = np.arange(80) / 100
    baseline = candidate + .2
    result = moving_block_bootstrap(candidate, baseline, n_resamples=50)
    assert result == moving_block_bootstrap(candidate, baseline, n_resamples=50)
    assert result['mean_improvement'] == pytest.approx(.2)
    assert result['lower'] == pytest.approx(.2)
    assert equal_weight_aggregate([{'mae': 1, 'n': 1000}, {'mae': 3, 'n': 10}], ['mae'])['mae'] == 2


def test_cancel_resume_fingerprint_and_candidate_roundtrip(tmp_path):
    data = history()
    interrupted = run_experiment(data, ticker='TEST', output=tmp_path,
                                 feature_sets=('baseline',), config=small_config(),
                                 cancelled=lambda: True)
    assert interrupted['status'] == 'cancelled'
    result = run_experiment(data, ticker='TEST', output=tmp_path,
                            feature_sets=('baseline',), config=small_config())
    assert result['status'] == 'completed'
    assert len(result['folds']) == 2
    candidate = result['candidate']
    assert candidate['independent_evaluation'] is False
    with pytest.raises(ValueError, match='cutoff'):
        predict_candidate(Path(candidate['path']), data)
    prediction = predict_candidate(Path(candidate['path']), history(421))
    assert 0 <= prediction['probability'] <= 1
    assert prediction['horizon'] == 5
    assert prediction['training_prior'] == candidate['training_prior']
    assert result == run_experiment(data, ticker='TEST', output=tmp_path,
                                    feature_sets=('baseline',), config=small_config())
    changed = data.copy()
    changed.loc[changed.index[-1], 'Volume'] += 1
    with pytest.raises(ValueError, match='fingerprint'):
        run_experiment(changed, ticker='TEST', output=tmp_path,
                       feature_sets=('baseline',), config=small_config())


def test_outer_outcomes_cannot_change_first_fold_selection(tmp_path):
    data = history()
    first = outer_splits(len(data), small_config())[0]
    changed = data.copy()
    # Perturb only held-out prices, preserving OHLC consistency.
    changed.iloc[first['test'][0]:, :4] *= 1.2
    kwargs = dict(ticker='TEST', feature_sets=('baseline',), config=small_config())
    a = run_experiment(data, output=tmp_path / 'a', **kwargs)
    b = run_experiment(changed, output=tmp_path / 'b', **kwargs)
    assert a['folds'][0]['selection'] == b['folds'][0]['selection']
    assert a['folds'][0]['calibration'] == b['folds'][0]['calibration']


def test_regression_separate_intervals_and_integrity(tmp_path):
    result = run_experiment(history(), ticker='TEST', output=tmp_path, task='regression',
                            feature_sets=('baseline',), config=small_config())
    prediction = predict_candidate(Path(result['candidate']['path']), history(421))
    assert prediction['lower_return'] <= prediction['upper_return']
    assert 0 <= result['folds'][0]['metrics']['interval_coverage'] <= 1
    path = Path(result['candidate']['path'])
    path.write_bytes(path.read_bytes() + b'corruption')
    with pytest.raises(ValueError, match='checksum'):
        predict_candidate(path, history())


def test_missing_context_is_not_silently_imputed(tmp_path):
    with pytest.raises(ValueError, match='context'):
        run_experiment(history(), ticker='AAPL', output=tmp_path, config=small_config())


def test_manual_gru_challenger_has_real_held_out_scores(tmp_path):
    from stock_app.research.experiments import run_gru_challenger, _Checkpoints, _feature_frames
    from stock_app.config import GRUConfig
    import time
    data = history()
    frames, valid = _feature_frames(data, None, ('baseline',))
    target = (np.log(data.Close.shift(-5) / data.Close) > .002).to_numpy(dtype=int)
    (tmp_path / 'checkpoints').mkdir()
    checkpoints = _Checkpoints(tmp_path, None, time.monotonic() + 60)
    config = GRUConfig(max_epochs=1, hidden_size=8, projection_size=8, head_size=8,
                       num_layers=1, device='cpu')
    result = run_gru_challenger(data, frames, valid, target, small_config(), checkpoints, config)
    assert result['status'] == 'completed'
    assert result['folds'][0]['metrics']['n'] > 0
    assert 0 <= result['folds'][0]['metrics']['brier'] <= 1


def context_history():
    data = history()
    # Explicit availability keeps synthetic weekday fixtures independent of holidays.
    data['AvailableAt'] = data.index.tz_localize('UTC') + pd.Timedelta(hours=21)
    return data, {'SPY': data.copy(), 'XLK': data.copy()}


def test_feature_comparison_is_matched_and_selection_excludes_outer_outcomes(tmp_path):
    data, contexts = context_history()
    kwargs = dict(ticker='AAPL', contexts=contexts, config=small_config())
    a = run_experiment(data, output=tmp_path / 'a', **kwargs)
    changed = data.copy()
    start = outer_splits(len(data), small_config())[0]['test'][0]
    changed.iloc[start:, :4] *= 1.2
    b = run_experiment(changed, output=tmp_path / 'b', **kwargs)
    for name in ('baseline', 'context'):
        left, right = a['folds'][0]['per_feature'][name], b['folds'][0]['per_feature'][name]
        assert left['selection'] == right['selection']
        assert left['calibration'] == right['calibration']
        assert left['selection']['feature_set'] == name
    for fold in a['folds']:
        baseline = fold['per_feature']['baseline']['observations']
        context = fold['per_feature']['context']['observations']
        assert [o['origin_time'] for o in baseline] == [o['origin_time'] for o in context]
        assert [o['actual'] for o in baseline] == [o['actual'] for o in context]
        comparison = fold['feature_comparison']
        assert comparison['paired_loss']['n'] == len(baseline)
        assert comparison['observations'][0]['baseline_prediction'] == baseline[0]['prediction']
        assert comparison['observations'][0]['context_prediction'] == context[0]['prediction']
        best = min(fold['trials'], key=lambda trial: trial['validation_loss'])
        assert fold['selection'] == best


def test_completed_report_enrichment_preserves_artifacts_and_reuses_selection(tmp_path, monkeypatch):
    import json
    from stock_app.research import experiments
    data, contexts = context_history()
    report = run_experiment(data, ticker='AAPL', output=tmp_path,
                            contexts=contexts, config=small_config())
    # Emulate a previously completed report without feature-specific evaluation.
    for fold in report['folds']:
        fold.pop('per_feature')
        fold.pop('feature_comparison')
    (tmp_path / 'report.json').write_text(json.dumps(report))
    pinned = {p: p.read_bytes() for p in [tmp_path / 'report.json', tmp_path / 'candidate.joblib',
                                          tmp_path / 'candidate.metadata.json', tmp_path / 'fold-0.json']}
    # Old runs have selection checkpoints but no per-feature calibrators.
    for path in (tmp_path / 'checkpoints').glob('*-feature-*'):
        path.unlink()
    def forbidden(*args, **kwargs):
        raise AssertionError('Existing selection fits must be reused')
    monkeypatch.setattr(experiments, '_fit_xgb', forbidden)
    paused = experiments.enrich_feature_comparison(data, output=tmp_path, contexts=contexts, time_budget=0)
    assert paused['status'] == 'paused'
    result = experiments.enrich_feature_comparison(data, output=tmp_path, contexts=contexts)
    assert result['status'] == 'completed'
    assert result['schema_version'] == 'feature-comparison-v1'
    assert set(result['aggregate']) == {'baseline', 'context'}
    assert result['folds'][0]['feature_comparison']['paired_loss']['n'] > 0
    assert all(path.read_bytes() == content for path, content in pinned.items())
    assert result == experiments.enrich_feature_comparison(data, output=tmp_path, contexts=contexts)
    changed = data.copy()
    changed.iloc[-1, changed.columns.get_loc('Volume')] += 1
    with pytest.raises(ValueError, match='fingerprint'):
        experiments.enrich_feature_comparison(changed, output=tmp_path, contexts=contexts)
