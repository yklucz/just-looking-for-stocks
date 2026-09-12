"""Align features/targets on raw candle origins and reuse Stage 1 purged splits."""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import DEFAULT_SPLIT, FeatureConfig, SplitConfig, TargetConfig
from ..features import FeatureResult, build_features
from ..features.validation import validate_history
from ..targets.target_builder import build_targets
from .time_split import TemporalSplit, chronological_split


@dataclass(frozen=True)
class FeatureDataset:
    features: FeatureResult
    X: pd.DataFrame
    y: pd.Series
    target_times: pd.Series
    split: TemporalSplit
    sequence_length: int
    target_config: TargetConfig

    def partition(self, name: str) -> tuple[pd.DataFrame, pd.Series]:
        if name not in {"train", "validation", "test"}:
            raise ValueError("Unknown partition")
        dates = self.features.all_features.index[getattr(self.split, name)]
        return self.X.loc[dates].copy(), self.y.loc[dates].copy()

    def sequences(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """Unscaled arrays for later sequential models; never bridge invalid candles."""
        _, y = self.partition(name)
        values = self.features.all_features.to_numpy()
        origins = getattr(self.split, name)
        x = np.stack([values[t - self.sequence_length + 1:t + 1] for t in origins])
        return x, y.to_numpy()


def build_feature_dataset(history: pd.DataFrame, feature_config: FeatureConfig = FeatureConfig(),
                          target_config: TargetConfig = TargetConfig(),
                          split_config: SplitConfig = DEFAULT_SPLIT,
                          sequence_length: int = 1,
                          explicit_split: TemporalSplit | None = None) -> FeatureDataset:
    """Targets and split boundaries count raw candles, never the compressed valid rows.

    Feature filtering cannot change t+h, split cutoffs, or sequence adjacency.
    No scaler/model is fitted here. All partitions must retain usable samples.
    """
    raw = validate_history(history, feature_config)
    features = build_features(raw, feature_config)
    targets = build_targets(raw.Close, target_config)
    split = explicit_split if explicit_split is not None else chronological_split(
        pd.DatetimeIndex(raw.index), sequence_length, target_config.horizon, split_config)
    if explicit_split is not None:
        _validate_explicit_split(split, len(raw), target_config.horizon)
    valid = features.valid_mask & raw.index.isin(targets.index)
    dates = raw.index[valid]
    X = features.all_features.loc[dates].copy()
    y = targets.target.loc[dates].copy()
    contiguous = features.valid_mask.rolling(sequence_length, min_periods=sequence_length).sum()
    eligible = (contiguous == sequence_length).to_numpy()
    partitions = {name: getattr(split, name)[eligible[getattr(split, name)]]
                  for name in ("train", "validation", "test")}
    if any(len(rows) == 0 for rows in partitions.values()):
        raise ValueError("Feature warm-up/invalid rows leave an empty train/validation/test partition")
    filtered = TemporalSplit(split.train_end, split.validation_end, **partitions)
    return FeatureDataset(features, X, y, targets.target_time.loc[dates].copy(),
                          filtered, sequence_length, target_config)


def _validate_explicit_split(split: TemporalSplit, length: int, horizon: int) -> None:
    """Reject caller-supplied splits that would bypass Stage 1 temporal guarantees."""
    if not 0 < split.train_end < split.validation_end < length:
        raise ValueError("Invalid explicit split boundaries")
    bounds = {"train": (0, split.train_end - horizon),
              "validation": (split.train_end, split.validation_end - horizon),
              "test": (split.validation_end, length - horizon)}
    for name, (start, end) in bounds.items():
        rows = np.asarray(getattr(split, name))
        if (rows.ndim != 1 or not len(rows) or rows.dtype.kind not in "iu"
                or (rows[1:] <= rows[:-1]).any() or rows[0] < start or rows[-1] >= end):
            raise ValueError(f"Invalid or unpurged explicit {name} origins")
