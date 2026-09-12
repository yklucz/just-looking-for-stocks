"""Descriptive fold aggregation; never used for fitting or threshold selection."""

from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..config import WalkForwardConfig
from ..models.metrics import classification_metrics


METRICS = (
    "accuracy",
    "balanced_accuracy",
    "precision",
    "recall",
    "f1",
    "roc_auc",
    "pr_auc",
    "brier_score",
    "log_loss",
    "directional_accuracy",
)

DISTRIBUTION_FIELDS = (
    "mean",
    "median",
    "std",
    "min",
    "max",
)


def distribution(
    values: Iterable[float | None],
) -> dict[str, float | int | None]:
    filtered = np.asarray(
        [
            value
            for value in values
            if value is not None
        ],
        dtype=float,
    )

    if filtered.size == 0:
        return {
            "count": 0,
            **dict.fromkeys(
                DISTRIBUTION_FIELDS,
                None,
            ),
        }

    return {
        "count": int(filtered.size),
        "mean": float(filtered.mean()),
        "median": float(
            np.median(filtered)
        ),
        "std": float(
            filtered.std(ddof=0)
        ),
        "min": float(filtered.min()),
        "max": float(filtered.max()),
    }


def probability_distribution(
    probabilities: Iterable[float],
) -> dict[str, float | int | None]:
    values = np.asarray(
        list(probabilities),
        dtype=float,
    )

    result = distribution(values)

    result.update(
        {
            "q25": float(
                np.quantile(
                    values,
                    0.25,
                )
            ),
            "q75": float(
                np.quantile(
                    values,
                    0.75,
                )
            ),
            "predicted_up_rate": float(
                (
                    values >= 0.5
                ).mean()
            ),
        }
    )

    return result


def _metric_distribution(
    metrics: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        key: distribution(
            metric[key]
            for metric in metrics
        )
        for key in METRICS
    }


def _auc_above_half(
    metrics: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    values = [
        metric["roc_auc"]
        for metric in metrics
        if metric["roc_auc"] is not None
    ]

    count = sum(
        value > 0.5
        for value in values
    )

    return {
        "count": count,
        "valid_folds": len(values),
        "fraction": (
            count / len(values)
            if values
            else None
        ),
    }


def _pr_above_prevalence(
    metrics: list[dict[str, Any]],
    folds: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    values = [
        (
            metric["pr_auc"],
            fold[
                "partitions"
            ][
                "test"
            ][
                "class_balance"
            ][
                "positive_ratio"
            ],
        )
        for metric, fold in zip(
            metrics,
            folds,
        )
        if metric["pr_auc"] is not None
    ]

    count = sum(
        pr_auc > prevalence
        for pr_auc, prevalence in values
    )

    return {
        "count": count,
        "valid_folds": len(values),
        "fraction": (
            count / len(values)
            if values
            else None
        ),
    }


def _stability_warnings(
    aggregate: dict[str, Any],
    config: WalkForwardConfig,
) -> list[str]:
    thresholds = (
        (
            "roc_auc",
            config.auc_std_warning,
        ),
        (
            "brier_score",
            config.brier_std_warning,
        ),
        (
            "f1",
            config.f1_std_warning,
        ),
    )

    warnings: list[str] = []

    for key, cutoff in thresholds:
        std = aggregate[key]["std"]

        if (
            std is not None
            and std > cutoff
        ):
            warnings.append(key)

    return warnings


def _brier_beats_prior(
    model: str,
    folds: list[dict[str, Any]],
) -> int | None:
    if model == "training_prior":
        return None

    if (
        "training_prior"
        not in folds[0]["models"]
    ):
        return None

    return sum(
        fold[
            "models"
        ][
            model
        ][
            "test"
        ][
            "brier_score"
        ]
        <
        fold[
            "models"
        ][
            "training_prior"
        ][
            "test"
        ][
            "brier_score"
        ]
        for fold in folds
    )


def aggregate_models(
    folds: list[dict[str, Any]],
    oof: pd.DataFrame,
    config: WalkForwardConfig,
) -> dict[str, Any]:
    if not folds:
        return {}

    result: dict[str, Any] = {}

    for model in folds[0]["models"]:
        metrics = [
            fold[
                "models"
            ][
                model
            ][
                "test"
            ]
            for fold in folds
        ]

        aggregate = (
            _metric_distribution(
                metrics
            )
        )

        probability_column = (
            f"{model}_probability"
        )

        model_result: dict[str, Any] = {
            "folds": len(folds),
            "aggregate": aggregate,
            "auc_above_half": (
                _auc_above_half(
                    metrics
                )
            ),
            "pr_above_prevalence": (
                _pr_above_prevalence(
                    metrics,
                    folds,
                )
            ),
            "stability_warnings": (
                _stability_warnings(
                    aggregate,
                    config,
                )
            ),
            "oof": (
                classification_metrics(
                    oof[
                        "actual_target"
                    ],
                    oof[
                        probability_column
                    ],
                    future_returns=oof[
                        "future_log_return"
                    ],
                )
            ),
            "probabilities": (
                probability_distribution(
                    oof[
                        probability_column
                    ]
                )
            ),
        }

        brier_result = (
            _brier_beats_prior(
                model,
                folds,
            )
        )

        if model != "training_prior":
            model_result[
                "brier_beats_training_prior_folds"
            ] = brier_result

        result[model] = model_result

    return result


def aggregate_importance(
    folds: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    for fold in folds:
        rows = fold.get(
            "xgboost_importance",
            [],
        )

        if not rows:
            continue

        gains = pd.Series(
            {
                str(row["feature"]): float(
                    row["gain"]
                )
                for row in rows
            },
            dtype=float,
        )

        ranks = gains.rank(
            ascending=False,
            method="average",
        )

        # Convert Series to a normal dict.
        #
        # Pylance types the key returned by Series.items()
        # as Hashable, which causes ranks[name] to fail
        # static type checking.
        rank_map: dict[str, float] = {
            str(feature): float(rank)
            for feature, rank
            in ranks.items()
        }

        top_features = {
            str(row["feature"])
            for row in rows[:10]
            if float(row["gain"]) > 0
        }

        for feature_key, gain in gains.items():
            feature = str(
                feature_key
            )

            records.append(
                {
                    "feature": feature,
                    "gain": float(gain),
                    "rank": rank_map[
                        feature
                    ],
                    "top10": int(
                        feature
                        in top_features
                    ),
                }
            )

    if not records:
        return []

    frame = pd.DataFrame(records)

    grouped = frame.groupby(
        "feature",
        sort=True,
    )

    result: list[dict[str, Any]] = []

    for feature, rows in grouped:
        result.append(
            {
                "feature": str(feature),
                "average_gain": float(
                    rows[
                        "gain"
                    ].mean()
                ),
                "median_rank": float(
                    rows[
                        "rank"
                    ].median()
                ),
                "top10_folds": int(
                    rows[
                        "top10"
                    ].sum()
                ),
                "folds": len(rows),
            }
        )

    return sorted(
        result,
        key=lambda row: (
            -row[
                "top10_folds"
            ],
            row[
                "median_rank"
            ],
            row[
                "feature"
            ],
        ),
    )