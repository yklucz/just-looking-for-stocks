import numpy as np
from .base import BinaryClassifier, class_balance


class MajorityClassifier(BinaryClassifier):
    """Hard majority decision; 0/1 probabilities are degenerate benchmark scores."""
    model_type = "majority"

    def fit(self, X, y):
        self._bind(X, y)
        self.balance = class_balance(y)
        self.positive_probability = float(self.balance["positive_ratio"] > .5)  # ties choose zero
        return self

    def predict_proba(self, X):
        self._check(X)
        return np.full(len(X), self.positive_probability)


class PriorClassifier(MajorityClassifier):
    """Training-frequency probability baseline; no random guesses."""
    model_type = "training_prior"

    def fit(self, X, y):
        super().fit(X, y)
        self.positive_probability = self.balance["positive_ratio"]
        return self


class MomentumClassifier(BinaryClassifier):
    """No learned parameters: bind the input schema, then use observed return_1."""
    model_type = "momentum"

    def fit(self, X, y):
        self._bind(X, y)
        if "return_1" not in X:
            raise ValueError("Momentum baseline requires causal return_1")
        return self

    def predict_proba(self, X):
        self._check(X)
        return (X.return_1.to_numpy() > 0).astype(float)
