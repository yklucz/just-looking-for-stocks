"""Binary research classifiers return one P(positive) value per input row."""
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


def validate_x(X: pd.DataFrame) -> None:
    if (not isinstance(X, pd.DataFrame) or X.empty or not X.columns.is_unique
            or not all(isinstance(c, str) for c in X.columns)):
        raise ValueError("Nonempty DataFrame with unique string feature names required")
    if (not isinstance(X.index, pd.DatetimeIndex) or X.index.hasnans
            or not X.index.is_unique or not X.index.is_monotonic_increasing):
        raise ValueError("Chronological unique feature timestamps required")
    if not np.isfinite(X.to_numpy(dtype=float)).all():
        raise ValueError("Model features must be finite")


def validate_y(X: pd.DataFrame, y: pd.Series, both_classes: bool = False) -> None:
    if not isinstance(y, pd.Series) or not X.index.equals(y.index):
        raise ValueError("Labels must align exactly with feature timestamps")
    if not y.isin([0, 1]).all() or (both_classes and y.nunique() != 2):
        raise ValueError("Binary labels required; learned classifiers need both TRAIN classes")


def class_balance(y: pd.Series) -> dict:
    if len(y) == 0 or not y.isin([0, 1]).all():
        raise ValueError("Nonempty binary labels required")
    positive = int(y.sum())
    return {"positive": positive, "negative": len(y) - positive, "positive_ratio": positive / len(y)}


class BinaryClassifier(ABC):
    model_type: str
    feature_names: tuple[str, ...] = ()

    def _bind(self, X: pd.DataFrame, y: pd.Series, both_classes: bool = False) -> None:
        validate_x(X)
        validate_y(X, y, both_classes)
        self.feature_names = tuple(X.columns)

    def _check(self, X: pd.DataFrame) -> None:
        validate_x(X)
        if not self.feature_names or tuple(X.columns) != self.feature_names:
            raise ValueError("Model feature names/order mismatch or model not fitted")

    @abstractmethod
    def fit(self, X: pd.DataFrame, y: pd.Series):
        ...

    @abstractmethod
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        ...

    def predict(self, X: pd.DataFrame, decision_threshold: float = 0.5) -> np.ndarray:
        if not 0 < decision_threshold < 1:
            raise ValueError("Decision probability threshold must be in (0, 1)")
        return (self.predict_proba(X) >= decision_threshold).astype(int)
