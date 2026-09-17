"""Resumable nested chronological experiments with untouched outer evaluation.

Every boundary is a raw candle position. A label at t uses t+5; rows with
labels reaching the next block are purged before any feature validity filter.
The production candidate has separate provenance from outer-fold predictions.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import time
import tempfile

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (balanced_accuracy_score, brier_score_loss, log_loss,
                             mean_absolute_error, mean_squared_error, roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from xgboost import XGBClassifier, XGBRegressor

from ..config import FeatureConfig
from ..features import build_features
from .statistics import equal_weight_aggregate, moving_block_bootstrap


def _pack(value):
    """Use XGBoost native weights rather than its platform-sensitive pickle state."""
    if isinstance(value, (XGBClassifier, XGBRegressor)):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.json'
            value.save_model(path)
            weights = path.read_bytes()
        return {'__native_xgboost__': 'classifier' if isinstance(value, XGBClassifier) else 'regressor',
                'parameters': value.get_params(), 'weights': weights}
    if isinstance(value, dict):
        return {key: _pack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_pack(item) for item in value)
    return value


def _unpack(value):
    if isinstance(value, dict) and '__native_xgboost__' in value:
        cls = XGBClassifier if value['__native_xgboost__'] == 'classifier' else XGBRegressor
        model = cls(**value['parameters'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.json'
            path.write_bytes(value['weights'])
            with threadpool_limits(limits=1, user_api='openmp'):
                model.load_model(path)
        return model
    if isinstance(value, dict):
        return {key: _unpack(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_unpack(item) for item in value)
    return value


@dataclass(frozen=True)
class ExperimentConfig:
    horizon: int = 5
    event_threshold: float = .002
    outer_blocks: int = 4
    block_size: int = 126
    calibration_size: int = 126
    validation_size: int = 126
    windows: tuple = (756, 1260, 'expanding')
    settings: tuple = (
        {'max_depth': 3, 'learning_rate': .05, 'min_child_weight': 3, 'reg_lambda': 1},
        {'max_depth': 2, 'learning_rate': .05, 'min_child_weight': 3, 'reg_lambda': 1},
        {'max_depth': 3, 'learning_rate': .05, 'min_child_weight': 10, 'reg_lambda': 10},
        {'max_depth': 2, 'learning_rate': .02, 'min_child_weight': 3, 'reg_lambda': 1},
    )
    n_estimators: int = 1000
    early_stopping_rounds: int = 30
    bootstrap_resamples: int = 1000
    seed: int = 42


def _validate_config(config):
    for name in ('horizon', 'outer_blocks', 'block_size', 'calibration_size',
                 'validation_size', 'n_estimators', 'early_stopping_rounds', 'bootstrap_resamples'):
        if getattr(config, name) < 1:
            raise ValueError(f'{name} must be positive')
    if any(getattr(config, name) <= config.horizon
           for name in ('block_size', 'calibration_size', 'validation_size')):
        raise ValueError('Blocks must be longer than horizon')
    if not config.windows or not config.settings or any(
            w != 'expanding' and (not isinstance(w, int) or w <= config.horizon)
            for w in config.windows):
        raise ValueError('Nonempty valid windows and settings required')


def _split(test_start, test_end, config):
    calibration_start = test_start - config.calibration_size
    validation_start = calibration_start - config.validation_size
    h = config.horizon
    return {'train': np.arange(0, validation_start - h),
            'validation': np.arange(validation_start, calibration_start - h),
            'calibration': np.arange(calibration_start, test_start - h),
            'test': np.arange(test_start, test_end - h),
            'train_end': validation_start, 'validation_end': calibration_start,
            'calibration_end': test_start, 'test_end': test_end}


def outer_splits(length, config=ExperimentConfig()):
    """Four trailing untouched outer blocks, preceded by validation/calibration."""
    _validate_config(config)
    first = length - config.outer_blocks * config.block_size
    available = first - config.calibration_size - config.validation_size
    required = max([w for w in config.windows if isinstance(w, int)] or [126])
    if available < required:
        raise ValueError(f'Insufficient history: need at least {required + length - available} candles')
    return [_split(first + i * config.block_size, first + (i + 1) * config.block_size, config)
            for i in range(config.outer_blocks)]


def _json_value(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (np.ndarray, pd.Index)):
        return value.tolist()
    if isinstance(value, (Path, pd.Timestamp)):
        return str(value)
    raise TypeError(f'Cannot serialize {type(value).__name__}')


def _json(data):
    return json.dumps(data, sort_keys=True, default=_json_value, allow_nan=False)


def _write_json(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(_json(data))
    temp.replace(path)


def _frame_hash(frame):
    h = sha256()
    h.update(_json({'columns': list(frame.columns), 'dtypes': list(map(str, frame.dtypes))}).encode())
    h.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return h.hexdigest()


def _fingerprints(history, contexts, config, ticker, task, feature_sets, include_gru):
    data = {'history': _frame_hash(history),
            'contexts': {str(k): _frame_hash(v) for k, v in sorted((contexts or {}).items())},
            'config': asdict(config), 'ticker': ticker, 'task': task,
            'feature_sets': feature_sets, 'include_gru': include_gru,
            'engine_version': 1, 'feature_config': asdict(FeatureConfig())}
    return {'experiment': sha256(_json(data).encode()).hexdigest(), 'inputs': data}


def _feature_frames(history, contexts, feature_sets, ticker='AAPL'):
    market = history.loc[:, [c for c in ('Open', 'High', 'Low', 'Close', 'Volume') if c in history]]
    base = build_features(market)
    result = {'baseline': base.all_features}
    if 'context' in feature_sets:
        from .features import build_context_features
        from .config import SECTOR_ETFS
        sector = SECTOR_ETFS.get(ticker, 'SPY')
        if not contexts or 'SPY' not in contexts or sector not in contexts:
            raise ValueError('Required market/sector context is unavailable')
        extra = build_context_features(history, contexts, sector=sector)
        result['context'] = pd.concat([base.all_features, extra], axis=1)
    frames = {name: result[name].replace([np.inf, -np.inf], np.nan) for name in feature_sets}
    valid = base.valid_mask.to_numpy().copy()
    for frame in frames.values():
        valid &= frame.notna().all(axis=1).to_numpy()
    if not valid.any():
        raise ValueError('No complete feature/context observations')
    return frames, valid


class _Stopped(Exception):
    def __init__(self, status):
        self.status = status


class _Checkpoints:
    def __init__(self, output, cancelled, deadline, progress_filename="progress.json"):
        self.output = output
        self.progress_filename = progress_filename
        self.cancelled = cancelled
        self.deadline = deadline
        self.completed = []

    def check(self):
        if self.cancelled and self.cancelled():
            raise _Stopped('cancelled')
        if time.monotonic() >= self.deadline:
            raise _Stopped('paused')

    def fit(self, key, fit):
        self.check()
        path = self.output / 'checkpoints' / (key + '.joblib')
        checksum = path.with_suffix('.sha256')
        if path.exists() and checksum.exists():
            if sha256(path.read_bytes()).hexdigest() != checksum.read_text():
                raise ValueError('Checkpoint checksum mismatch')
            value = _unpack(joblib.load(path))
        else:
            with threadpool_limits(limits=1, user_api='blas'):
                value = fit()
            temp = path.with_suffix('.tmp')
            joblib.dump(_pack(value), temp)
            temp.replace(path)
            checksum.write_text(sha256(path.read_bytes()).hexdigest())
        self.completed.append(key)
        _write_json(self.output / self.progress_filename, {'completed_fits': self.completed})
        return value


def _classifier_probability(model, x):
    values = model.predict_proba(x)
    classes = list(model.classes_)
    if 1 not in classes:
        return np.zeros(len(x))
    return values[:, classes.index(1)]


def _fit_xgb(x, y, xv, yv, settings, config, task, quantile=None):
    params = dict(settings, n_estimators=config.n_estimators,
                  early_stopping_rounds=config.early_stopping_rounds,
                  n_jobs=1, random_state=config.seed, tree_method='hist')
    if task == 'binary' and len(np.unique(y)) < 2:
        return DummyClassifier(strategy='prior').fit(x, y)
    if task == 'binary':
        model = XGBClassifier(**params, objective='binary:logistic', eval_metric='logloss')
    elif quantile is not None:
        model = XGBRegressor(**params, objective='reg:quantileerror', quantile_alpha=quantile,
                             eval_metric='quantile')
    else:
        model = XGBRegressor(**params, objective='reg:squarederror', eval_metric='rmse')
    # Series conversion follows the existing model adapter and avoids a native
    # XGBoost/macOS metadata path that crashes with raw float64 NumPy labels.
    model.fit(x, pd.Series(y, index=x.index),
              eval_set=[(xv, pd.Series(yv, index=xv.index))], verbose=False)
    return model


def _predict(model, x, task):
    return _classifier_probability(model, x) if task == 'binary' else model.predict(x)


def _loss(y, p, task):
    return float(log_loss(y, np.clip(p, 1e-7, 1-1e-7), labels=[0, 1])) if task == 'binary' else float(mean_squared_error(y, p))


def _calibrate(model, x, y):
    p = np.clip(_classifier_probability(model, x), 1e-7, 1-1e-7)
    logits = np.log(p / (1-p)).reshape(-1, 1)
    if len(np.unique(y)) < 2:
        return {'constant': float(np.mean(y))}
    return LogisticRegression(C=1e6, solver='lbfgs', random_state=42).fit(logits, y)


def _calibrated(model, calibrator, x):
    p = np.clip(_classifier_probability(model, x), 1e-7, 1-1e-7)
    if isinstance(calibrator, dict):
        return np.full(len(x), calibrator['constant'])
    return calibrator.predict_proba(np.log(p/(1-p)).reshape(-1, 1))[:, 1]


def _metrics(y, predictions, task, close=None, lower=None, upper=None):
    if task == 'binary':
        p = np.clip(predictions, 1e-7, 1-1e-7)
        return {'brier': float(brier_score_loss(y, p)),
                'log_loss': float(log_loss(y, p, labels=[0, 1])),
                'auc': float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
                'balanced_accuracy': float(balanced_accuracy_score(y, p >= .5)), 'n': len(y)}
    result = {'mae': float(mean_absolute_error(y, predictions)),
              'rmse': float(np.sqrt(mean_squared_error(y, predictions))), 'n': len(y)}
    if close is not None:
        actual_price, estimate_price = close * np.exp(y), close * np.exp(predictions)
        result.update(price_mae=float(mean_absolute_error(actual_price, estimate_price)),
                      price_rmse=float(np.sqrt(mean_squared_error(actual_price, estimate_price))))
    if lower is not None:
        result.update(interval_coverage=float(np.mean((y >= lower) & (y <= upper))),
                      mean_interval_width=float(np.mean(upper-lower)), nominal_coverage=.8)
    return result


def _train_rows(split, window, valid):
    start = 0 if window == 'expanding' else max(0, split['train_end'] - window)
    rows = split['train']
    rows = rows[(rows >= start) & valid[rows]]
    if len(rows) < 10:
        raise ValueError('Feature warm-up leaves fewer than ten training observations')
    return rows


def _rows(split, name, valid):
    rows = split[name]
    rows = rows[valid[rows]]
    if not len(rows):
        raise ValueError(f'Empty {name} block after feature validity filtering')
    return rows


def _selection(split, frames, target, valid, config, task, checkpoints, prefix):
    vr = _rows(split, 'validation', valid)
    trials = []
    best = None
    for feature_set, frame in frames.items():
        for wi, window in enumerate(config.windows):
            tr = _train_rows(split, window, valid)
            for si, settings in enumerate(config.settings):
                model = checkpoints.fit(f'{prefix}-{feature_set}-w{wi}-s{si}',
                    lambda: _fit_xgb(frame.iloc[tr], target[tr], frame.iloc[vr], target[vr],
                                     settings, config, task))
                loss = _loss(target[vr], _predict(model, frame.iloc[vr], task), task)
                info = {'feature_set': feature_set, 'window': window, 'setting': si,
                        'parameters': settings, 'validation_loss': loss,
                        'best_iteration': int(getattr(model, 'best_iteration', config.n_estimators-1))}
                trials.append(info)
                if best is None or loss < best[0]:
                    best = loss, info, model, tr
    return best[1], best[2], best[3], trials


def _baseline_predictions(frame, target, train_rows, test_rows, close, config, task, checkpoints, prefix):
    prior = float(np.mean(target[train_rows]))
    if task == 'regression':
        return {'unchanged_price': np.zeros(len(test_rows)),
                'training_mean': np.full(len(test_rows), prior)}
    def fit_logistic():
        x, y = frame.iloc[train_rows], target[train_rows]
        estimator = DummyClassifier(strategy='prior') if len(np.unique(y)) < 2 else LogisticRegression(
            C=1, max_iter=1000, random_state=config.seed)
        return make_pipeline(SimpleImputer(strategy='median', keep_empty_features=True),
                             StandardScaler(), estimator).fit(x, y)
    model = checkpoints.fit(prefix + '-logistic', fit_logistic)
    momentum = np.log(close / close.shift(config.horizon)).fillna(0).to_numpy()
    return {'training_prior': np.full(len(test_rows), prior),
            'majority_class': np.full(len(test_rows), float(prior >= .5)),
            'momentum': (momentum[test_rows] > config.event_threshold).astype(float),
            'logistic_regression': _classifier_probability(model, frame.iloc[test_rows])}


def _intervals(split, frame, target, train_rows, config, selection, checkpoints, prefix):
    vr = split['validation']
    vr = vr[frame.iloc[vr].notna().all(axis=1).to_numpy()]
    return [checkpoints.fit(prefix + f'-quantile-{quantile}',
                lambda q=quantile: _fit_xgb(frame.iloc[train_rows], target[train_rows],
                    frame.iloc[vr], target[vr], selection['parameters'], config, 'regression', q))
            for quantile in (.1, .9)]


def _boundary_metadata(split, history, config):
    metadata = {}
    for name in ('train', 'validation', 'calibration', 'test'):
        rows = split[name]
        if len(rows):
            metadata[name] = {'origin_start': str(history.index[rows[0]]),
                              'origin_end': str(history.index[rows[-1]]),
                              'label_end': str(history.index[rows[-1] + config.horizon]),
                              'raw_origin_start': int(rows[0]), 'raw_origin_end': int(rows[-1])}
    return metadata


def _evaluate_configuration(number, split, frames, target, valid, history, config, task, checkpoints,
                            feature_set=None):
    prefix = f'fold-{number}'
    choices = frames if feature_set is None else {feature_set: frames[feature_set]}
    selection, model, tr, trials = _selection(split, choices, target, valid, config, task, checkpoints, prefix)
    # Reuse selection fits, but isolate each configuration's calibrator/intervals.
    if feature_set is not None:
        prefix += '-feature-' + feature_set
    frame = frames[selection['feature_set']]
    cr, er = _rows(split, 'calibration', valid), _rows(split, 'test', valid)
    calibration = {'origin_start': str(history.index[cr[0]]), 'origin_end': str(history.index[cr[-1]]),
                   'label_end': str(history.index[cr[-1]+config.horizon])}
    lower = upper = None
    if task == 'binary':
        calibrator = checkpoints.fit(prefix + '-calibration', lambda: _calibrate(model, frame.iloc[cr], target[cr]))
        prediction = _calibrated(model, calibrator, frame.iloc[er])
        calibration['method'] = 'sigmoid' if not isinstance(calibrator, dict) else 'single_class_prior'
    else:
        prediction = model.predict(frame.iloc[er])
        low_model, high_model = _intervals(split, frame, target, tr, config, selection, checkpoints, prefix)
        low_raw, high_raw = low_model.predict(frame.iloc[er]), high_model.predict(frame.iloc[er])
        lower, upper = np.minimum(low_raw, high_raw), np.maximum(low_raw, high_raw)
        calibration['method'] = 'not_applicable_to_regression'
    close = history.Close.iloc[er].to_numpy()
    metrics = _metrics(target[er], prediction, task, close, lower, upper)
    baseline_predictions = _baseline_predictions(frames.get('baseline', frame), target, tr, er,
                                                 history.Close, config, task, checkpoints, prefix)
    baselines, comparisons = {}, {}
    for name, values in baseline_predictions.items():
        baselines[name] = _metrics(target[er], values, task, close)
        loss = (target[er]-prediction)**2 if task == 'binary' else np.abs(target[er]-prediction)
        baseline_loss = (target[er]-values)**2 if task == 'binary' else np.abs(target[er]-values)
        comparisons[name] = moving_block_bootstrap(loss, baseline_loss,
                                                   n_resamples=config.bootstrap_resamples, seed=config.seed)
        comparisons[name]['loss'] = 'brier' if task == 'binary' else 'absolute_log_return_error'
    observations = []
    for j, raw in enumerate(er):
        row = {'origin_time': str(history.index[raw]), 'target_time': str(history.index[raw+config.horizon]),
               'raw_origin': int(raw), 'actual': float(target[raw]), 'prediction': float(prediction[j]),
               'origin_close': float(close[j]),
               'baselines': {name: float(values[j]) for name, values in baseline_predictions.items()}}
        if lower is not None:
            row.update(lower_return=float(lower[j]), upper_return=float(upper[j]))
        observations.append(row)
    return {'fold': number, 'selection': selection, 'trials': trials,
            'boundaries': _boundary_metadata(split, history, config), 'calibration': calibration,
            'metrics': metrics, 'baselines': baselines, 'comparisons': comparisons,
            'observations': observations}


def _compare_features(per_feature, task, config):
    """Pair feature variants on identical untouched origins; positive favors context."""
    if not {'baseline', 'context'}.issubset(per_feature):
        return None
    baseline = per_feature['baseline']['observations']
    context = per_feature['context']['observations']
    if len(baseline) != len(context) or any(
            (a['raw_origin'], a['origin_time'], a['target_time'], a['actual']) !=
            (b['raw_origin'], b['origin_time'], b['target_time'], b['actual'])
            for a, b in zip(baseline, context)):
        raise ValueError('Feature comparison requires exactly matched outer origins and outcomes')
    actual = np.array([row['actual'] for row in baseline])
    bp = np.array([row['prediction'] for row in baseline])
    cp = np.array([row['prediction'] for row in context])
    errors = lambda prediction: ((prediction-actual)**2 if task == 'binary'
                                  else np.abs(prediction-actual))
    paired = moving_block_bootstrap(errors(cp), errors(bp),
                                   n_resamples=config.bootstrap_resamples, seed=config.seed)
    paired['loss'] = 'brier' if task == 'binary' else 'absolute_log_return_error'
    differences = {}
    for metric, base_value in per_feature['baseline']['metrics'].items():
        context_value = per_feature['context']['metrics'].get(metric)
        if metric in {'n', 'nominal_coverage'} or base_value is None or context_value is None:
            continue
        differences[metric] = float(context_value - base_value)
    return {'comparison': 'context versus baseline', 'paired_loss': paired,
            'improvement_direction': 'positive paired_loss.mean_improvement favors context',
            'metric_differences_context_minus_baseline': differences,
            'observations': [dict(origin_time=a['origin_time'], target_time=a['target_time'],
                                  raw_origin=a['raw_origin'], actual=a['actual'],
                                  baseline_prediction=a['prediction'], context_prediction=b['prediction'])
                             for a, b in zip(baseline, context)]}


def _evaluate_features(number, split, frames, target, valid, history, config, task, checkpoints):
    per_feature = {name: _evaluate_configuration(number, split, frames, target, valid, history,
                                                config, task, checkpoints, feature_set=name)
                   for name in frames}
    return {'per_feature': per_feature, 'feature_comparison': _compare_features(per_feature, task, config)}


def _evaluate_fold(number, split, frames, target, valid, history, config, task, checkpoints):
    selected = _evaluate_configuration(number, split, frames, target, valid, history, config, task, checkpoints)
    selected.update(_evaluate_features(number, split, frames, target, valid, history, config, task, checkpoints))
    return selected


def enrich_feature_comparison(history, *, output, contexts=None, cancelled=None, time_budget=7200):
    """Write a versioned addendum to a completed run without changing its artifacts.

    Existing selection checkpoints are loaded with their checksums. Separate
    calibration/quantile fits are checkpointed with feature-specific names. The
    original report, fold files and production candidate remain byte-for-byte
    unchanged. A completed addendum is also immutable and tied to its report hash.
    """
    output = Path(output)
    source_path = output / 'report.json'
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    if source.get('status') != 'completed':
        raise ValueError('Feature enrichment requires a completed experiment')
    values = dict(source['config'])
    values['windows'] = tuple(values['windows'])
    values['settings'] = tuple(values['settings'])
    config = ExperimentConfig(**values)
    inputs = source['fingerprints']['inputs']
    feature_sets = tuple(inputs['feature_sets'])
    fingerprints = _fingerprints(history, contexts, config, source['ticker'], source['task'],
                                 feature_sets, inputs['include_gru'])
    if json.loads(_json(fingerprints)) != source['fingerprints']:
        raise ValueError('Experiment fingerprint mismatch for feature enrichment')
    source_hash = sha256(source_bytes).hexdigest()
    path = output / 'feature-comparison-v1.json'
    if path.exists():
        previous = json.loads(path.read_text())
        if previous['source_report_sha256'] != source_hash:
            raise ValueError('Feature addendum source report checksum mismatch')
        if previous['status'] == 'completed':
            return previous
    result = {'schema_version': 'feature-comparison-v1', 'status': 'running',
              'ticker': source['ticker'], 'task': source['task'],
              'source_report_sha256': source_hash, 'fingerprints': fingerprints,
              'selection_rule': 'Window and settings selected within each feature set using validation only',
              'evaluation_kind': 'historical_outer_holdout', 'folds': []}
    checkpoints = _Checkpoints(output, cancelled, time.monotonic()+max(0, time_budget),
                               progress_filename='feature-comparison-progress.json')
    try:
        checkpoints.check()
        frames, valid = _feature_frames(history, contexts, feature_sets, source['ticker'])
        returns = np.log(history.Close.shift(-config.horizon)/history.Close).to_numpy()
        target = (returns > config.event_threshold).astype(float) if source['task'] == 'binary' else returns
        for number, split in enumerate(outer_splits(len(history), config)):
            fold = _evaluate_features(number, split, frames, target, valid, history, config,
                                      source['task'], checkpoints)
            result['folds'].append(dict(fold=number, **fold))
            _write_json(path, result)
        result['aggregate'] = {
            name: equal_weight_aggregate([fold['per_feature'][name]['metrics'] for fold in result['folds']])
            for name in feature_sets}
        result['aggregation'] = 'Equal weight per outer fold; paired confidence intervals reported per fold'
        result['status'] = 'completed'
    except _Stopped as stopped:
        result['status'] = stopped.status
    result = json.loads(_json(result))
    _write_json(path, result)
    return result


def _candidate(frames, target, valid, history, config, task, fingerprints, checkpoints, ticker):
    # Latest calibration labels finish at the final observed candle. There is no
    # holdout after this fit; its performance must be measured prospectively.
    split = _split(len(history), len(history), config)
    selection, model, tr, trials = _selection(split, frames, target, valid, config, task, checkpoints, 'production')
    frame = frames[selection['feature_set']]
    cr = _rows(split, 'calibration', valid)
    calibrator = None
    low_model = high_model = None
    if task == 'binary':
        calibrator = checkpoints.fit('production-calibration', lambda: _calibrate(model, frame.iloc[cr], target[cr]))
    else:
        low_model, high_model = _intervals(split, frame, target, tr, config, selection, checkpoints, 'production')
    path = checkpoints.output / 'candidate.joblib'
    metadata = {'artifact_version': 1, 'ticker': ticker, 'task': task,
                'feature_set': selection['feature_set'], 'feature_config': asdict(FeatureConfig()),
                'columns': list(frame.columns), 'training_prior': float(np.mean(target[tr])),
                'fingerprints': fingerprints, 'horizon': config.horizon,
                'event_threshold': config.event_threshold, 'selection': selection,
                'active_cutoff': str(history.index[-1]),
                'calibration_label_end': str(history.index[cr[-1]+config.horizon]),
                'training_label_end': str(history.index[tr[-1]+config.horizon]),
                'history_start': str(history.index[0]), 'history_end': str(history.index[-1]),
                'independent_evaluation': False,
                'evaluation_note': 'Outer scores evaluate the selection procedure, not this final candidate.'}
    bundle = dict(metadata, model=model, calibrator=calibrator,
                  lower_model=low_model, upper_model=high_model)
    temp = path.with_suffix('.tmp')
    joblib.dump(_pack(bundle), temp)
    temp.replace(path)
    checksum = sha256(path.read_bytes()).hexdigest()
    _write_json(path.with_suffix('.metadata.json'), dict(metadata, artifact_sha256=checksum))
    return dict(metadata, path=str(path.resolve()), artifact_sha256=checksum)


def run_gru_challenger(history, frames, valid, target, config, checkpoints, gru_config=None):
    """Manual fixed GRU benchmark on purged outer blocks; never promoted."""
    from ..config import GRUConfig
    from ..models.gru_model import GRUClassifier
    from ..training.sequence_dataset import SequenceInput, valid_sequence_origins
    settings = gru_config or GRUConfig(device='cpu')
    frame = frames['baseline']
    eligible = valid & valid_sequence_origins(frame, settings.sequence_length).to_numpy()
    results = []
    for number, split in enumerate(outer_splits(len(history), config)):
        def evaluate(split=split, number=number):
            tr = _train_rows(split, 'expanding', eligible)
            vr, cr, er = (_rows(split, name, eligible) for name in ('validation', 'calibration', 'test'))
            def inputs(rows, training=False):
                return SequenceInput(frame, frame.index[rows], settings.sequence_length,
                                     frame.iloc[tr] if training else None)
            estimator = GRUClassifier(settings)
            estimator.fit(inputs(tr, True), pd.Series(target[tr], index=frame.index[tr]),
                          validation=(inputs(vr), pd.Series(target[vr], index=frame.index[vr])))
            calibration = np.clip(estimator.predict_proba(inputs(cr)), 1e-7, 1-1e-7)
            prediction = np.clip(estimator.predict_proba(inputs(er)), 1e-7, 1-1e-7)
            if len(np.unique(target[cr])) == 2:
                calibrator = LogisticRegression(C=1e6, random_state=42).fit(
                    np.log(calibration / (1-calibration)).reshape(-1, 1), target[cr])
                prediction = calibrator.predict_proba(np.log(prediction/(1-prediction)).reshape(-1, 1))[:, 1]
            else:
                prediction = np.full(len(er), float(np.mean(target[cr])))
            prior = np.full(len(er), float(np.mean(target[tr])))
            return {'fold': number, 'metrics': _metrics(target[er], prediction, 'binary'),
                    'training_prior': _metrics(target[er], prior, 'binary'),
                    'origins': [str(date) for date in frame.index[er]],
                    'comparison': moving_block_bootstrap((prediction-target[er])**2, (prior-target[er])**2,
                                                         n_resamples=config.bootstrap_resamples)}
        results.append(checkpoints.fit(f'gru-fold-{number}', evaluate))
    return {'requested': True, 'status': 'completed', 'configuration': asdict(settings),
            'folds': results, 'aggregate': equal_weight_aggregate([row['metrics'] for row in results]),
            'note': 'Fixed baseline-feature GRU challenger; origins listed; never automatically promoted.'}


def run_experiment(history, *, ticker, output, contexts=None, feature_sets=('baseline', 'context'),
                   task='binary', cancelled=None, time_budget=7200, include_gru=False, config=None):
    """Run/checkpoint all tabular fits; resume only with identical data/config.

    ``config`` is an explicit test/research override; defaults implement the
    registered four-block daily horizon-five plan without shrinking it silently.
    """
    config = config or ExperimentConfig()
    if task not in ('binary', 'regression') or not feature_sets or set(feature_sets)-{'baseline', 'context'}:
        raise ValueError('Unknown task or feature set')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'checkpoints').mkdir(exist_ok=True)
    fingerprints = _fingerprints(history, contexts, config, ticker, task, feature_sets, include_gru)
    manifest = output / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text()) != json.loads(_json(fingerprints)):
            raise ValueError('Experiment fingerprint mismatch; use a new output directory')
    else:
        _write_json(manifest, fingerprints)
    report_path = output / 'report.json'
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if report.get('status') == 'completed':
            return report
    checkpoints = _Checkpoints(output, cancelled, time.monotonic()+max(0, time_budget))
    report = {'status': 'running', 'ticker': ticker, 'task': task, 'config': asdict(config),
              'fingerprints': fingerprints, 'folds': [], 'candidate': None,
              'gru': {'requested': include_gru, 'status': 'not_requested' if not include_gru else 'pending'}}
    try:
        checkpoints.check()
        splits = outer_splits(len(history), config)
        frames, valid = _feature_frames(history, contexts, feature_sets, ticker)
        returns = np.log(history.Close.shift(-config.horizon)/history.Close).to_numpy()
        target = (returns > config.event_threshold).astype(float) if task == 'binary' else returns
        for number, split in enumerate(splits):
            fold_path = output / f'fold-{number}.json'
            if fold_path.exists():
                fold = json.loads(fold_path.read_text())
                if 'per_feature' not in fold:
                    # Resume older partial runs without rewriting their saved fold evidence.
                    fold.update(_evaluate_features(number, split, frames, target, valid, history,
                                                   config, task, checkpoints))
            else:
                fold = _evaluate_fold(number, split, frames, target, valid, history, config, task, checkpoints)
                _write_json(fold_path, fold)
            report['folds'].append(fold)
            _write_json(report_path, report)
        report['aggregate'] = equal_weight_aggregate([fold['metrics'] for fold in report['folds']])
        report['feature_aggregate'] = {
            name: equal_weight_aggregate([fold['per_feature'][name]['metrics'] for fold in report['folds']])
            for name in feature_sets}
        report['aggregation'] = 'equal_weight_per_outer_fold; aggregate tickers with equal_weight_aggregate'
        report['candidate'] = _candidate(frames, target, valid, history, config, task, fingerprints, checkpoints, ticker)
        if include_gru:
            if task != 'binary':
                raise ValueError('The manual GRU challenger supports binary classification only')
            report['gru'] = run_gru_challenger(history, frames, valid, target, config, checkpoints)
        report['status'] = 'completed'
    except _Stopped as stopped:
        report['status'] = stopped.status
    report['checkpoints'] = sorted(path.stem for path in (output / 'checkpoints').glob('*.joblib'))
    report = json.loads(_json(report))
    _write_json(report_path, report)
    return report


def predict_candidate(path, history, contexts=None):
    """Verify the artifact hash before loading a trusted local joblib bundle."""
    path = Path(path)
    metadata = json.loads(path.with_suffix('.metadata.json').read_text())
    if sha256(path.read_bytes()).hexdigest() != metadata['artifact_sha256']:
        raise ValueError('Candidate checksum mismatch')
    bundle = _unpack(joblib.load(path))
    if bundle['artifact_version'] != 1:
        raise ValueError('Unsupported candidate artifact version')
    if str(history.index[0]) != bundle['history_start']:
        raise ValueError('Prediction history must retain the original feature-history starting point')
    frames, valid = _feature_frames(history, contexts, (bundle['feature_set'],), bundle['ticker'])
    if not valid[-1]:
        raise ValueError('Latest origin does not have valid features')
    frame = frames[bundle['feature_set']]
    if list(frame.columns) != bundle['columns']:
        raise ValueError('Candidate feature schema mismatch')
    x = frame.iloc[[-1]]
    origin = pd.Timestamp(history.index[-1])
    if origin <= pd.Timestamp(bundle['active_cutoff']):
        raise ValueError('Prediction origin must follow candidate fitting cutoff')
    from .ledger import session_times
    _, _, target, _ = session_times(origin, bundle['horizon'])
    result = {'origin_time': str(origin), 'target_time': str(target),
              'target_time_kind': 'exchange_session',
              'horizon': bundle['horizon'], 'training_prior': bundle['training_prior'],
              'origin_close': float(history.Close.iloc[-1]), 'active_cutoff': bundle['active_cutoff'],
              'trained_until': bundle['calibration_label_end'], 'base_training_label_end': bundle['training_label_end'],
              'calibrated': bundle['calibrator'] is not None}
    with threadpool_limits(limits=1, user_api='blas'):
        if bundle['task'] == 'binary':
            result['probability'] = float(_calibrated(bundle['model'], bundle['calibrator'], x)[0])
        else:
            predicted = float(bundle['model'].predict(x)[0])
            low, high = float(bundle['lower_model'].predict(x)[0]), float(bundle['upper_model'].predict(x)[0])
            result.update(predicted_return=predicted, lower_return=min(low, high), upper_return=max(low, high))
    return result
