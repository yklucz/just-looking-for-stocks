import numpy as np
import pandas as pd

from ..config import TargetConfig


def validate_prices(prices: pd.Series) -> None:
    """Reject bad inputs instead of silently sorting, filling or dropping candles."""
    index = prices.index
    if not isinstance(index, pd.DatetimeIndex) or index.hasnans:
        raise ValueError("Prices require valid datetime timestamps")
    if not index.is_unique or not index.is_monotonic_increasing:
        raise ValueError("Timestamps must be unique and strictly chronological")
    values = prices.to_numpy(dtype=float)
    if not len(values) or not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("Prices must be nonempty, finite and positive")


def build_targets(prices: pd.Series, config: TargetConfig = TargetConfig()) -> pd.DataFrame:
    """X ends at t; y uses t+h observed candles, never calendar-day offsets.

    The final h rows have no known target and are removed, including for labels.
    Three-class encoding: 0=SELL, 1=HOLD, 2=BUY. Threshold is in log-return units.
    legacy_price exists only to preserve the current dashboard during Stage 1.
    """
    validate_prices(prices)
    h = config.horizon
    current = prices.iloc[:-h].to_numpy(dtype=float)
    future = prices.iloc[h:].to_numpy(dtype=float)
    returns = np.log(future) - np.log(current)
    if config.task == "regression":
        target = returns
    elif config.task == "binary":
        target = (returns > config.threshold).astype(np.int64)
    elif config.task == "three_class":
        target = np.where(returns < -config.threshold, 0,
                          np.where(returns > config.threshold, 2, 1))
    else:
        target = future
    return pd.DataFrame({"target": target, "future_log_return": returns,
                         "target_time": prices.index[h:]}, index=prices.index[:-h])
