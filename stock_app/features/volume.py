"""Trailing volume measures; undefined zero denominators remain NaN."""
import pandas as pd
from ..config import FeatureConfig
from .common import FeatureColumns, divide


def build_volume(volume: pd.Series, config: FeatureConfig) -> FeatureColumns:
    means = {n: volume.rolling(n, min_periods=n).mean()
             for n in dict.fromkeys(config.volume_windows + (config.volume_zscore_window,))}
    columns = {"volume_change": (divide(volume, volume.shift(1)) - 1, 2)}
    columns.update({f"volume_mean_{n}": (means[n], n) for n in config.volume_windows})
    columns.update({f"relative_volume_{n}": (divide(volume, means[n]), n) for n in config.volume_windows})
    n = config.volume_zscore_window
    std = volume.rolling(n, min_periods=n).std(ddof=1)
    columns[f"volume_zscore_{n}"] = (divide(volume - means[n], std), n)
    return columns
