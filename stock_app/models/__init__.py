"""Research models and saved-model inference."""
import sys

if sys.platform == 'darwin':
    # Mixed Apple OpenMP wheels require torch before sklearn/XGBoost.
    import torch

from .logistic import LogisticClassifier
from .naive import MajorityClassifier, MomentumClassifier, PriorClassifier
from .xgboost_model import XGBoostClassifier

__all__ = ["LogisticClassifier", "MajorityClassifier", "MomentumClassifier", "PriorClassifier", "XGBoostClassifier"]
