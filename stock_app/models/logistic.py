from dataclasses import asdict
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..config import LogisticConfig
from .base import BinaryClassifier


class LogisticClassifier(BinaryClassifier):
    model_type = "logistic"

    def __init__(self, config: LogisticConfig = LogisticConfig()):
        self.config = config

    def fit(self, X, y):
        self._bind(X, y, both_classes=True)
        # Default lbfgs/L2; no deprecated penalty argument needed across sklearn versions.
        self.estimator = make_pipeline(
            StandardScaler(), LogisticRegression(**asdict(self.config)), memory=None
        )
        self.estimator.fit(X, y)
        return self

    def predict_proba(self, X):
        self._check(X)
        return self.estimator.predict_proba(X)[:, 1]
