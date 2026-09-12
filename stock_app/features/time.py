"""Cyclical calendar features in the supplied index timezone; lookback 1."""
import numpy as np
import pandas as pd
from ..config import FeatureConfig
from .common import FeatureColumns


def build_time(index: pd.DatetimeIndex, config: FeatureConfig) -> FeatureColumns:
    cycles = [("day_of_week", index.dayofweek, 7), ("month", index.month - 1, 12)]
    if config.interval in {"1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h"}:
        cycles += [("hour", index.hour, 24), ("minute", index.minute, 60)]
    columns = {}
    for name, values, period in cycles:
        angle = 2 * np.pi * values / period
        columns[f"{name}_sin"] = (pd.Series(np.sin(angle), index=index), 1)
        columns[f"{name}_cos"] = (pd.Series(np.cos(angle), index=index), 1)
    return columns
