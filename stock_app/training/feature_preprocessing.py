"""Optional neural-input scaling, separate from causal features and tabular data."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

from .feature_dataset import FeatureDataset


@dataclass(frozen=True)
class FeaturePreprocessor:
    scaler: MinMaxScaler
    feature_names: tuple[str, ...]
    trained_until: pd.Timestamp

    def transform(self, features: pd.DataFrame) -> pd.DataFrame:
        if tuple(features.columns) != self.feature_names:
            raise ValueError("Feature names/order differ from fitted preprocessing schema")
        _validate_features(features)
        return pd.DataFrame(self.scaler.transform(features), index=features.index, columns=features.columns)


def _validate_features(features: pd.DataFrame) -> None:
    if (not isinstance(features.index, pd.DatetimeIndex) or features.index.hasnans
            or not features.index.is_unique or not features.index.is_monotonic_increasing):
        raise ValueError("Feature timestamps must be valid, unique and chronological")
    if features.empty or not np.isfinite(features.to_numpy(dtype=float)).all():
        raise ValueError("Scaling requires nonempty, finite model-valid features")


def fit_feature_preprocessor(dataset: FeatureDataset) -> FeaturePreprocessor:
    """Fit only usable TRAIN origins; validation/test/inference call transform only."""
    train, _ = dataset.partition("train")
    _validate_features(train)
    scaler = MinMaxScaler().fit(train)
    return FeaturePreprocessor(scaler, tuple(train.columns), train.index[-1])
