from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import DEFAULT_SPLIT, SplitConfig


@dataclass(frozen=True)
class TemporalSplit:
    train_end: int
    validation_end: int
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray


def chronological_split(index: pd.DatetimeIndex, lookback: int, horizon: int,
                        config: SplitConfig = DEFAULT_SPLIT) -> TemporalSplit:
    """Split raw candles, then purge origins whose labels cross a boundary.

    Arrays contain prediction origins t, not target timestamps. Evaluation may
    use earlier candles as sequence context, but never as future observations.
    """
    if (not isinstance(index, pd.DatetimeIndex) or index.hasnans
            or not index.is_unique or not index.is_monotonic_increasing):
        raise ValueError("Timestamps must be valid, unique and chronological")
    if type(lookback) is not int or lookback < 1 or type(horizon) is not int or horizon < 1:
        raise ValueError("lookback and horizon must be positive integers")
    n = len(index)
    train_end = int(n * config.train_fraction)
    validation_end = int(n * (config.train_fraction + config.validation_fraction))
    origins = np.arange(lookback - 1, n - horizon)
    train = origins[origins + horizon < train_end]
    validation = origins[(origins >= train_end) & (origins + horizon < validation_end)]
    test = origins[origins >= validation_end]
    if any(len(part) == 0 for part in (train, validation, test)):
        raise ValueError("Not enough historical data for nonempty purged train/validation/test splits")
    return TemporalSplit(train_end, validation_end, train, validation, test)
