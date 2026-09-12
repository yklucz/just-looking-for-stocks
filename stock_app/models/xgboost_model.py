from dataclasses import asdict
from typing import cast

from ..config import XGBoostConfig
from .base import BinaryClassifier, class_balance, validate_y


class XGBoostClassifier(BinaryClassifier):
    model_type = "xgboost"
    uses_validation = True

    def __init__(
        self,
        config: XGBoostConfig = XGBoostConfig(),
    ):
        self.config = config

    def fit(
        self,
        X,
        y,
        *,
        validation=None,
    ):
        from xgboost import XGBClassifier

        self._bind(
            X,
            y,
            both_classes=True,
        )

        if validation is None:
            raise ValueError(
                "Chronological validation required for XGBoost early stopping"
            )

        x_val, y_val = validation

        self._check(x_val)
        validate_y(
            x_val,
            y_val,
        )

        if X.index[-1] >= x_val.index[0]:
            raise ValueError(
                "Validation must be strictly after training origins"
            )

        params = asdict(
            self.config
        )

        balanced = params.pop(
            "balance_classes"
        )

        balance = class_balance(
            y
        )

        self.scale_pos_weight = (
            balance["negative"]
            / balance["positive"]
            if balanced
            else 1.0
        )

        self.estimator = XGBClassifier(
            **params,
            objective="binary:logistic",
            eval_metric="logloss",
            tree_method="hist",
            importance_type="gain",
            scale_pos_weight=(
                self.scale_pos_weight
            ),
        )

        self.estimator.fit(
            X,
            y,
            eval_set=[
                (
                    x_val,
                    y_val,
                )
            ],
            verbose=False,
        )

        return self

    def predict_proba(
        self,
        X,
    ):
        self._check(X)

        return self.estimator.predict_proba(
            X
        )[:, 1]

    def feature_importance(
        self,
    ) -> list[dict]:
        """
        Return feature gain importance using the same
        best-iteration trees used for prediction.
        """
        best = (
            self.estimator.best_iteration
        )

        gains = (
            self.estimator
            .get_booster()[
                : best + 1
            ]
            .get_score(
                importance_type="gain"
            )
        )

        importance: list[dict] = []

        for name in self.feature_names:
            # XGBoost's typing allows:
            #
            # float | list[float]
            #
            # because some importance types may return
            # vector-valued values.
            #
            # With importance_type="gain", this value is
            # scalar, so make that fact explicit for Pylance.
            gain = cast(
                float,
                gains.get(
                    name,
                    0.0,
                ),
            )

            importance.append(
                {
                    "feature": name,
                    "gain": float(
                        gain
                    ),
                }
            )

        return sorted(
            importance,
            key=lambda row: (
                -row["gain"],
                row["feature"],
            ),
        )