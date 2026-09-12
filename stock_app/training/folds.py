"""Deterministic origin windows and horizon-aware purging on the raw timeline."""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from ..config import WalkForwardConfig
from .feature_dataset import FeatureDataset
from .time_split import TemporalSplit


@dataclass(frozen=True)
class Fold:
    fold_id: int
    train_start: int
    train_end: int
    validation_end: int
    test_end: int
    split: TemporalSplit


def generate_folds(index: pd.DatetimeIndex, horizon: int,
                   config: WalkForwardConfig = WalkForwardConfig()) -> list[Fold]:
    if (not isinstance(index, pd.DatetimeIndex) or index.hasnans or not index.is_unique
            or not index.is_monotonic_increasing):
        raise ValueError("Chronological unique timestamps required")
    if type(horizon) is not int or horizon < 1:
        raise ValueError("Positive integer horizon required")
    if min(config.minimum_train_samples, config.validation_samples, config.rolling_train_samples) <= horizon:
        raise ValueError("Training and validation windows must exceed the horizon")
    folds = []
    train_end = config.minimum_train_samples
    while config.max_folds is None or len(folds) < config.max_folds:
        validation_end = train_end + config.validation_samples
        test_end = validation_end + config.test_samples
        # Every TEST origin needs a known t+h outcome, including the final row.
        if test_end + horizon > len(index):
            break
        start = 0 if config.mode == "expanding" else train_end - config.rolling_train_samples
        train = np.arange(start, train_end - horizon)
        validation = np.arange(train_end, validation_end - horizon)
        test = np.arange(validation_end, test_end)
        split = TemporalSplit(train_end, validation_end, train, validation, test)
        folds.append(Fold(len(folds), start, train_end, validation_end, test_end, split))
        train_end += config.step_samples
    if not folds:
        raise ValueError("Insufficient candles for one complete walk-forward fold")
    return folds


def dataset_for_fold(dataset: FeatureDataset, fold: Fold,
                     eligible_index: pd.DatetimeIndex | None = None) -> FeatureDataset:
    """Reuse full-history features; filter origins, never recompute/reset indicators."""
    if dataset.sequence_length != 1:
        raise ValueError("Stage 4 classifiers use tabular origins (sequence_length=1)")
    index = dataset.features.all_features.index
    eligible = index.isin(dataset.X.index)
    if eligible_index is not None:
        eligible &= index.isin(eligible_index)
    parts = {name: getattr(fold.split, name)[eligible[getattr(fold.split, name)]]
             for name in ("train", "validation", "test")}
    if any(len(values) == 0 for values in parts.values()):
        raise ValueError(f"Fold {fold.fold_id} has an empty partition after feature filtering")
    split = TemporalSplit(fold.train_end, fold.validation_end, **parts)
    view = replace(dataset, split=split)
    train_x, _ = view.partition("train")
    val_x, _ = view.partition("validation")
    test_x, _ = view.partition("test")
    if (view.target_times.loc[train_x.index].max() >= val_x.index[0]
            or view.target_times.loc[val_x.index].max() >= test_x.index[0]):
        raise ValueError("Purged label boundary violation")
    return view
