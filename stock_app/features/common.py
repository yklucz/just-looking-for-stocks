"""Shared numeric operations; each feature carries its minimum candle count."""
import numpy as np
import pandas as pd

FeatureColumns = dict[str, tuple[pd.Series, int]]


def divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return (numerator / denominator.where(denominator != 0)).replace([np.inf, -np.inf], np.nan)


def ema(values: pd.Series, window: int) -> pd.Series:
    """Recursive EMA seeded at first observation; output waits for window samples."""
    return values.ewm(span=window, adjust=False, min_periods=window).mean()


def wilder(values: pd.Series, window: int) -> pd.Series:
    """SMA seed followed by Wilder alpha=1/window recursion; values start at first delta."""
    if len(values) < window:
        return pd.Series(np.nan, index=values.index, dtype=float)
    seeded = values.iloc[window - 1:].copy()
    seeded.iloc[0] = values.iloc[:window].mean(skipna=False)
    return seeded.ewm(alpha=1 / window, adjust=False).mean().reindex(values.index)
