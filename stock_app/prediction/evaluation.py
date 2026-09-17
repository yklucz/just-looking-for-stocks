"""Resolved-outcome diagnostics; never fits models or selects parameters."""
import numpy as np
import pandas as pd
from ..config import TargetConfig
from ..models.metrics import classification_metrics
from ..targets.target_builder import build_targets


def evaluate_predictions(close, predictions, *, horizon, threshold, available_after, task):
    """Score only post-validation origins with an observed t+h outcome.

    Targets count raw candles before alignment. Recent unresolved forecasts are
    counted separately. These retrospective diagnostics are not a prospective
    live trading record; adjacent multi-session outcomes overlap.
    """
    if task not in {'binary', 'regression'}:
        raise ValueError('Unsupported evaluation task')
    if (not isinstance(predictions, pd.Series) or not isinstance(predictions.index, pd.DatetimeIndex)
            or predictions.index.hasnans or not predictions.index.is_unique
            or not predictions.index.is_monotonic_increasing
            or not np.isfinite(predictions).all()):
        raise ValueError('Chronological predictions with finite values required')
    if task == 'binary' and ((predictions < 0) | (predictions > 1)).any():
        raise ValueError('Probability must be in [0, 1]')
    targets = build_targets(close, TargetConfig(horizon=horizon, threshold=threshold, task=task))
    after = pd.to_datetime(available_after, utc=True)
    predictions = predictions.loc[pd.to_datetime(predictions.index, utc=True) > after]
    if not predictions.index.isin(close.index).all():
        raise ValueError('Prediction origins must exist in observed history')
    resolved = predictions.index.intersection(targets.index)
    result = {'status': 'ready' if len(resolved) else 'insufficient_data',
              'samples': len(resolved), 'pending_samples': len(predictions) - len(resolved),
              'available_after': after.isoformat(), 'horizon': horizon, 'metrics': None,
              'note': 'Retrospective frozen-model diagnostics after validation; overlapping outcomes are dependent. Not a live forecast track record or evidence of future accuracy.'}
    if not len(resolved):
        return result
    p = predictions.loc[resolved].to_numpy(dtype=float)
    actual = targets.loc[resolved, 'future_log_return'].to_numpy(dtype=float)
    result.update(origin_start=resolved[0].isoformat(), origin_end=resolved[-1].isoformat(),
                  outcome_end=targets.loc[resolved[-1], 'target_time'].isoformat())
    if task == 'binary':
        y = targets.loc[resolved, 'target'].to_numpy()
        result['metrics'] = classification_metrics(y, p)
        # Fixed always-event rule, not the winning class selected on test data.
        result['baseline'] = {'name': 'Always predict the return event', 'accuracy': float(y.mean())}
        result['event_frequency'] = float(y.mean())
    else:
        origin = close.loc[resolved].to_numpy(dtype=float)
        realized = origin * np.exp(actual)
        with np.errstate(over='ignore'):
            estimated = origin * np.exp(p)
        if not np.isfinite(estimated).all() or (estimated <= 0).any():
            raise ValueError('Price estimates must be finite and positive')
        error = estimated - realized
        baseline_error = origin - realized
        result['metrics'] = {
            'price_mae': float(np.mean(np.abs(error))),
            'price_rmse': float(np.sqrt(np.mean(error ** 2))),
            'price_mape_percent': float(100 * np.mean(np.abs(error) / realized)),
            'log_return_mae': float(np.mean(np.abs(p - actual))),
            'directional_accuracy': float(np.mean(np.sign(p) == np.sign(actual))),
        }
        result['baseline'] = {'name': 'Unchanged Close (zero return)',
                              'price_mae': float(np.mean(np.abs(baseline_error))),
                              'price_rmse': float(np.sqrt(np.mean(baseline_error ** 2))),
                              'price_mape_percent': float(100 * np.mean(np.abs(baseline_error) / realized))}
        result['beats_baseline_mae'] = result['metrics']['price_mae'] < result['baseline']['price_mae']
    return result
