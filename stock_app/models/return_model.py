"""Independent future-log-return regression; never converts class probabilities to prices."""
from dataclasses import asdict
import numpy as np
import pandas as pd
from ..config import XGBoostConfig
from .base import validate_x


class XGBoostReturnRegressor:
    model_type = 'xgboost_regressor'
    uses_validation = True

    def __init__(self, config: XGBoostConfig = XGBoostConfig()):
        self.config = config
        self.feature_names = ()

    def _check(self, X):
        validate_x(X)
        if tuple(X.columns) != self.feature_names:
            raise ValueError('Model feature names/order mismatch')

    def fit(self, X, y, *, validation):
        from xgboost import XGBRegressor
        validate_x(X)
        self.feature_names = tuple(X.columns)
        xv, yv = validation
        self._check(xv)
        for inputs, targets in ((X, y), (xv, yv)):
            if not isinstance(targets, pd.Series) or not inputs.index.equals(targets.index) or not np.isfinite(targets).all():
                raise ValueError('Finite aligned regression targets required')
        if X.index[-1] >= xv.index[0]:
            raise ValueError('Validation must follow training')
        params = asdict(self.config)
        params.pop('balance_classes')
        self.estimator = XGBRegressor(**params, objective='reg:squarederror', eval_metric='rmse', tree_method='hist')
        self.estimator.fit(X, y, eval_set=[(xv, yv)], verbose=False)
        return self

    def predict(self, X):
        self._check(X)
        return self.estimator.predict(X)
