"""Trailing SMA and recursive EMA; minimum observation counts are attached."""
import pandas as pd
from ..config import FeatureConfig
from .common import FeatureColumns, divide, ema


def build_trend(close: pd.Series, config: FeatureConfig) -> FeatureColumns:
    sma = {n: close.rolling(n, min_periods=n).mean()
           for n in dict.fromkeys(config.sma_windows + config.sma_ratio_windows)}
    averages = {n: ema(close, n) for n in dict.fromkeys(config.ema_windows + config.ema_ratio_pair)}
    columns = {f"sma_{n}": (sma[n], n) for n in config.sma_windows}
    columns.update({f"ema_{n}": (averages[n], n) for n in config.ema_windows})
    columns.update({f"close_sma_{n}_ratio": (divide(close, sma[n]) - 1, n)
                    for n in config.sma_ratio_windows})
    columns.update({f"close_ema_{n}_ratio": (divide(close, averages[n]) - 1, n)
                    for n in config.ema_windows})
    fast, slow = config.ema_ratio_pair
    columns[f"ema_{fast}_ema_{slow}_ratio"] = (divide(averages[fast], averages[slow]) - 1, max(fast, slow))
    return columns
