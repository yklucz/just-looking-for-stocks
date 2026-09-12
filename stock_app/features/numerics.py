"""Numerical comparisons shared by OHLC validation and prediction ingestion."""
import numpy as np


def exceeds_price_bound(value, bound):
    """Ignore only floating-point roundoff, never market-sized price differences.

    Adjusted OHLC fields can follow different multiply/divide paths. Permit
    eight float64 epsilons relative to the two prices (~1.8e-15), without
    changing either value. This comparison depends only on the current row.
    """
    tolerance = 8 * np.finfo(np.float64).eps * np.maximum(np.abs(value), np.abs(bound))
    with np.errstate(invalid='ignore'):
        return (value - bound) > tolerance
