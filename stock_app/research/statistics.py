"""Paired uncertainty estimates that retain local serial dependence."""
import numpy as np


def moving_block_bootstrap(candidate_errors, baseline_errors, block_size=20,
                           n_resamples=1000, seed=42) -> dict:
    """Positive improvement means baseline loss minus candidate loss is positive.

    Resample paired contiguous blocks, not independent observations. Confidence
    limits describe this observed series, not uncertainty from model selection.
    """
    candidate = np.asarray(candidate_errors, dtype=float)
    baseline = np.asarray(baseline_errors, dtype=float)
    if candidate.ndim != 1 or candidate.shape != baseline.shape or not len(candidate):
        raise ValueError('Paired nonempty one-dimensional errors required')
    if not np.isfinite(candidate).all() or not np.isfinite(baseline).all():
        raise ValueError('Errors must be finite')
    if block_size < 1 or n_resamples < 1:
        raise ValueError('Positive block_size and n_resamples required')
    difference = baseline - candidate
    size = min(int(block_size), len(difference))
    rng = np.random.default_rng(seed)
    means = np.empty(n_resamples)
    for i in range(n_resamples):
        starts = rng.integers(0, len(difference) - size + 1,
                              size=int(np.ceil(len(difference) / size)))
        indices = (starts[:, None] + np.arange(size)).ravel()[:len(difference)]
        means[i] = difference[indices].mean()
    lower, upper = np.quantile(means, [.025, .975])
    return {'mean_improvement': float(difference.mean()), 'lower': float(lower),
            'upper': float(upper), 'block_size': size, 'n_resamples': n_resamples,
            'n': len(difference), 'seed': seed, 'confidence': .95}


def equal_weight_aggregate(rows, metrics=None) -> dict:
    """Average each requested metric per ticker/fold, never by row count."""
    rows = list(rows)
    if not rows:
        return {}
    if metrics is None:
        metrics = sorted(set.intersection(*(set(row) for row in rows)) - {'n', 'seed'})
    result = {}
    for metric in metrics:
        values = [row.get(metric) for row in rows]
        finite = [float(v) for v in values
                  if isinstance(v, (int, float, np.number)) and np.isfinite(v)]
        result[metric] = float(np.mean(finite)) if finite else None
    return result
