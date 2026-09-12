from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler
from sklearn.utils.validation import check_is_fitted

from ..config import DEFAULT_SPLIT, SplitConfig, TargetConfig
from ..targets.target_builder import build_targets, validate_prices
from .time_split import TemporalSplit, chronological_split


@dataclass
class PreparedData:
    prices: pd.Series
    targets: pd.DataFrame
    split: TemporalSplit
    scaler: MinMaxScaler
    scaled: np.ndarray
    lookback: int
    target_config: TargetConfig

    def samples(self, partition: str) -> tuple[np.ndarray, np.ndarray]:
        if partition not in {"train", "validation", "test"}:
            raise ValueError("Unknown partition")
        origins = getattr(self.split, partition)
        x = np.stack([self.scaled[t - self.lookback + 1:t + 1] for t in origins])
        y = self.targets["target"].iloc[origins].to_numpy()
        if self.target_config.task == "legacy_price":
            y = self.scaler.transform(y.reshape(-1, 1))[:, 0]
        return x.astype(np.float32), y


def transform_inference(prices: pd.Series, scaler: MinMaxScaler) -> np.ndarray:
    """Transform only. Values outside the training range are intentionally unclipped."""
    validate_prices(prices)
    check_is_fitted(scaler)
    return scaler.transform(prices.to_numpy(dtype=float).reshape(-1, 1)).astype(np.float32)


def inference_window(prices: pd.Series, scaler: MinMaxScaler, lookback: int) -> np.ndarray:
    if type(lookback) is not int or lookback < 1 or len(prices) < lookback:
        raise ValueError("Not enough history for the inference lookback")
    return transform_inference(prices, scaler)[-lookback:].reshape(1, lookback, 1)


def prepare_training(prices: pd.Series, lookback: int,
                     target: TargetConfig = TargetConfig(),
                     split_config: SplitConfig = DEFAULT_SPLIT,
                     scaler: MinMaxScaler | None = None) -> PreparedData:
    """Fit once on raw TRAIN rows, or reuse a verified artifact scaler without fitting."""
    validate_prices(prices)
    split = chronological_split(pd.DatetimeIndex(prices.index), lookback,
                                target.horizon, split_config)
    train_values = prices.iloc[:split.train_end].to_numpy(dtype=float).reshape(-1, 1)
    if scaler is None:
        scaler = MinMaxScaler()
        scaler.fit(train_values)
    else:
        check_is_fitted(scaler)
        if (scaler.n_samples_seen_ != len(train_values)
                or not np.array_equal(scaler.data_min_, train_values.min(axis=0))
                or not np.array_equal(scaler.data_max_, train_values.max(axis=0))):
            raise ValueError("Saved scaler does not match TRAIN observations")
    return PreparedData(prices.copy(), build_targets(prices, target), split, scaler,
                        transform_inference(prices, scaler), lookback, target)
