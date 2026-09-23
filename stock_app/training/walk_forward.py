"""Fresh Stage 3 classifiers per chronological fold; diagnostic OOF, no trading."""

import argparse
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import random
import re
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from ..config import FeatureConfig, TargetConfig, WalkForwardConfig
from ..features.validation import validate_history
from ..models import (
    LogisticClassifier,
    MajorityClassifier,
    MomentumClassifier,
    PriorClassifier,
    XGBoostClassifier,
)
from ..models.base import class_balance
from ..models.metrics import classification_metrics
from ..models.registry import canonical, fingerprint, model_contract, save_model
from ..targets.target_builder import build_targets
from .feature_dataset import build_feature_dataset
from .fold_results import (
    aggregate_importance,
    aggregate_models,
    probability_distribution,
)
from .folds import dataset_for_fold, generate_folds
from .model_inputs import (
    evaluate_partitioned,
    fit_partitioned,
    model_partition,
)
from .train_baselines import runtime_provenance
from .walk_forward_report import write_artifacts


logger = logging.getLogger(__name__)

ModelFactory = Callable[[], Any]


def default_factories() -> dict[str, ModelFactory]:
    return {
        "majority": MajorityClassifier,
        "training_prior": PriorClassifier,
        "momentum": MomentumClassifier,
        "logistic": LogisticClassifier,
        "xgboost": XGBoostClassifier,
    }


def _hash_pandas_object(
    value: Any,
    *,
    include_index: bool = False,
) -> str:
    """Return a stable SHA-256 digest for a pandas object."""
    hashed = pd.util.hash_pandas_object(
        value,
        index=include_index,
    )

    payload = hashed.to_numpy(
        dtype=np.uint64,
        copy=False,
    ).tobytes()

    return hashlib.sha256(payload).hexdigest()


def _validate_ticker(ticker: str) -> None:
    if not re.fullmatch(
        r"[A-Za-z0-9.^=_-]{1,32}",
        ticker,
    ):
        raise ValueError("Invalid ticker identifier")

    if ticker in {".", ".."}:
        raise ValueError("Invalid ticker identifier")


def _prepare_history(
    history: pd.DataFrame,
    feature_config: FeatureConfig,
) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    raw = validate_history(
        history,
        feature_config,
    ).copy()

    # Make the DatetimeIndex type explicit for both runtime and Pylance.
    raw_index = pd.DatetimeIndex(raw.index)
    raw.index = raw_index

    return raw, raw_index


def _build_configuration(
    *,
    ticker: str,
    feature_config: FeatureConfig,
    target_config: TargetConfig,
    config: WalkForwardConfig,
    dataset: Any,
    eligible_index: pd.DatetimeIndex | None,
) -> dict[str, Any]:
    matched_rows: dict[str, Any] | None = None

    if eligible_index is not None:
        matched_rows = {
            "count": len(eligible_index),
            "sha256": _hash_pandas_object(
                eligible_index,
            ),
        }

    return canonical(
        {
            "version": "walk-forward-v1",
            "ticker": ticker.upper(),
            "interval": feature_config.interval,
            "walk_forward": asdict(config),
            "target": asdict(target_config),
            "decision_threshold": 0.5,
            "features": dataset.features.metadata,
            "matched_rows": matched_rows,
            "models": {},
        }
    )


def _create_run_root(
    output: Path,
    ticker: str,
    interval: str,
    horizon: int,
) -> Path:
    run_id = (
        datetime.now(timezone.utc).strftime(
            "%Y%m%dT%H%M%S"
        )
        + "_"
        + uuid4().hex[:8]
    )

    root = (
        Path(output)
        / ticker.upper()
        / interval
        / f"h{horizon}"
        / "walk_forward"
        / run_id
    )

    root.mkdir(
        parents=True,
        exist_ok=False,
    )

    return root


def _build_provenance(
    raw: pd.DataFrame,
    raw_index: pd.DatetimeIndex,
    source: str,
) -> dict[str, Any]:
    provenance: dict[str, Any] = runtime_provenance()

    provenance.update(
        source=source,
        raw_data_sha256=_hash_pandas_object(
            raw,
            include_index=True,
        ),
        index_timezone=(
            str(raw_index.tz)
            if raw_index.tz is not None
            else None
        ),
    )

    return provenance


def _new_models(
    factories: dict[str, ModelFactory],
) -> dict[str, Any]:
    models = {
        name: factory()
        for name, factory in factories.items()
    }

    model_ids = {
        id(model)
        for model in models.values()
    }

    if len(model_ids) != len(models):
        raise ValueError(
            "Every fold requires fresh, unfitted model instances"
        )

    if any(
        model.feature_names
        for model in models.values()
    ):
        raise ValueError(
            "Every fold requires fresh, unfitted model instances"
        )

    return models


def _model_signatures(
    models: dict[str, Any],
) -> dict[str, Any]:
    return canonical(
        {
            name: {
                "type": model.model_type,
                "parameters": (
                    asdict(model.config)
                    if hasattr(model, "config")
                    else {}
                ),
            }
            for name, model in models.items()
        }
    )


def _validate_fold_model_configuration(
    fold_id: int,
    configuration: dict[str, Any],
    models: dict[str, Any],
) -> None:
    signatures = _model_signatures(models)

    if fold_id == 0:
        configuration["models"] = signatures
        return

    if signatures != configuration["models"]:
        raise ValueError(
            "Model configuration changed between folds"
        )


def _empty_fold_result(
    fold: Any,
) -> dict[str, Any]:
    return {
        "fold_id": fold.fold_id,
        "raw_boundaries": {
            "train_start": fold.train_start,
            "train_end_exclusive": fold.train_end,
            "validation_end_exclusive": (
                fold.validation_end
            ),
            "test_end_exclusive": fold.test_end,
        },
        "models": {},
        "partitions": {},
        "xgboost_importance": [],
    }


def _fit_fold_models(
    models: dict[str, Any],
    view: Any,
    returns: pd.Series,
    result: dict[str, Any],
) -> None:
    for name, model in models.items():
        seed = getattr(
            getattr(
                model,
                "config",
                None,
            ),
            "random_state",
            42,
        )

        random.seed(seed)
        np.random.seed(seed)

        started = perf_counter()

        fit_partitioned(
            model,
            view,
        )

        # Explicit Any prevents Pylance from incorrectly inferring
        # this as dict[str, float].
        scores: dict[str, Any] = {
            "fit_seconds": (
                perf_counter() - started
            ),
            "train": evaluate_partitioned(
                model,
                view,
                "train",
                returns,
            ),
            "validation": evaluate_partitioned(
                model,
                view,
                "validation",
                returns,
            ),
        }

        if hasattr(
            model,
            "training_diagnostics",
        ):
            scores["training_diagnostics"] = (
                model.training_diagnostics
            )

        if isinstance(
            model,
            XGBoostClassifier,
        ):
            best_iteration = int(
                model.estimator.best_iteration
            )

            scores["selection"] = {
                "best_iteration": best_iteration,
                "number_of_trees": (
                    best_iteration + 1
                ),
                "grown_trees": (
                    model.estimator
                    .get_booster()
                    .num_boosted_rounds()
                ),
                "validation_log_loss": (
                    scores["validation"][
                        "log_loss"
                    ]
                ),
            }

            result["xgboost_importance"] = (
                model.feature_importance()
            )

        result["models"][name] = scores


def _build_oof_frame(
    *,
    ticker: str,
    fold: Any,
    raw_index: pd.DatetimeIndex,
    view: Any,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    returns: pd.Series,
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ticker.upper(),
            "fold_id": fold.fold_id,
            "raw_position": (
                raw_index.get_indexer(
                    x_test.index,
                )
            ),
            "actual_target": y_test,
            "future_log_return": (
                returns.loc[x_test.index]
            ),
            "target_time": (
                view.target_times.loc[
                    x_test.index
                ]
            ),
            "model_available_after": (
                raw_index[
                    fold.validation_end - 1
                ]
            ),
        },
        index=x_test.index,
    )


def _score_test_models(
    *,
    models: dict[str, Any],
    view: Any,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    returns: pd.Series,
    result: dict[str, Any],
    oof: pd.DataFrame,
    ticker: str,
    config: WalkForwardConfig,
    fold: Any,
    root: Path,
    provenance: dict[str, Any],
) -> None:
    for name, model in models.items():
        # Labels are intentionally unused here.
        test_inputs, _ = model_partition(
            model,
            view,
            "test",
        )

        if not test_inputs.index.equals(
            x_test.index
        ):
            raise ValueError(
                "Sequence warm-up changes test origins; "
                "an explicit matched-row protocol is required"
            )

        probabilities = model.predict_proba(
            test_inputs
        )

        scores: dict[str, Any] = (
            result["models"][name]
        )

        scores["test"] = (
            classification_metrics(
                y_test,
                probabilities,
                future_returns=returns.loc[
                    x_test.index
                ],
            )
        )

        scores["probabilities"] = (
            probability_distribution(
                probabilities
            )
        )

        if hasattr(
            model,
            "last_inference_ms",
        ):
            scores["inference_ms"] = (
                model.last_inference_ms
            )

        oof[f"{name}_probability"] = (
            probabilities
        )

        oof[f"{name}_prediction"] = (
            probabilities >= 0.5
        ).astype(int)

        contract = model_contract(
            model,
            view,
            ticker,
            {
                **asdict(config),
                "fold_id": fold.fold_id,
                "train_start": (
                    fold.train_start
                ),
            },
        )

        scores["fingerprint"] = (
            fingerprint(contract)
        )

        scores["artifact"] = str(
            Path("models")
            / f"fold_{fold.fold_id:03d}"
            / name
        )

        save_model(
            model,
            root / scores["artifact"],
            contract,
            scores,
            {
                **provenance,
                "fold_id": fold.fold_id,
                "decision_threshold": 0.5,
            },
        )


def _partition_summaries(
    view: Any,
) -> dict[str, Any]:
    partitions: dict[str, Any] = {}

    for partition_name in (
        "train",
        "validation",
        "test",
    ):
        x_part, y_part = view.partition(
            partition_name
        )

        target_times = (
            view.target_times.loc[
                x_part.index
            ]
        )

        partitions[partition_name] = {
            "samples": len(x_part),
            "origin_start": (
                x_part.index[0].isoformat()
            ),
            "origin_end": (
                x_part.index[-1].isoformat()
            ),
            "label_end": (
                target_times
                .max()
                .isoformat()
            ),
            "class_balance": (
                class_balance(y_part)
            ),
        }

    return partitions


def _fold_diagnostics(
    x_test: pd.DataFrame,
    returns: pd.Series,
) -> dict[str, float | None]:
    return {
        "mean_future_log_return": float(
            returns.loc[
                x_test.index
            ].mean()
        ),
        "mean_volatility_20": (
            float(
                x_test[
                    "volatility_20"
                ].mean()
            )
            if "volatility_20" in x_test
            else None
        ),
        "mean_observed_return_1": (
            float(
                x_test[
                    "return_1"
                ].mean()
            )
            if "return_1" in x_test
            else None
        ),
    }


def _run_fold(
    *,
    fold: Any,
    dataset: Any,
    eligible_index: (
        pd.DatetimeIndex | None
    ),
    factories: dict[
        str,
        ModelFactory,
    ],
    configuration: dict[str, Any],
    returns: pd.Series,
    ticker: str,
    raw_index: pd.DatetimeIndex,
    config: WalkForwardConfig,
    root: Path,
    provenance: dict[str, Any],
) -> tuple[
    dict[str, Any],
    pd.DataFrame,
]:
    view = dataset_for_fold(
        dataset,
        fold,
        eligible_index,
    )

    # y_train and y_val were unused.
    x_train, _ = view.partition(
        "train"
    )

    x_val, _ = view.partition(
        "validation"
    )

    models = _new_models(
        factories
    )

    _validate_fold_model_configuration(
        fold.fold_id,
        configuration,
        models,
    )

    result = _empty_fold_result(
        fold
    )

    _fit_fold_models(
        models,
        view,
        returns,
        result,
    )

    # No candidate's test prediction is used until
    # all fold models have been fitted.
    x_test, y_test = view.partition(
        "test"
    )

    oof = _build_oof_frame(
        ticker=ticker,
        fold=fold,
        raw_index=raw_index,
        view=view,
        x_test=x_test,
        y_test=y_test,
        returns=returns,
    )

    _score_test_models(
        models=models,
        view=view,
        x_test=x_test,
        y_test=y_test,
        returns=returns,
        result=result,
        oof=oof,
        ticker=ticker,
        config=config,
        fold=fold,
        root=root,
        provenance=provenance,
    )

    result["partitions"] = (
        _partition_summaries(
            view
        )
    )

    result["diagnostics"] = (
        _fold_diagnostics(
            x_test,
            returns,
        )
    )

    logger.info(
        (
            "%s %s fold %d "
            "train=%d val=%d test=%d"
        ),
        ticker,
        config.mode,
        fold.fold_id,
        len(x_train),
        len(x_val),
        len(x_test),
    )

    return result, oof


def _run_all_folds(
    *,
    folds: list[Any],
    dataset: Any,
    eligible_index: (
        pd.DatetimeIndex | None
    ),
    factories: dict[
        str,
        ModelFactory,
    ],
    configuration: dict[str, Any],
    returns: pd.Series,
    ticker: str,
    raw_index: pd.DatetimeIndex,
    config: WalkForwardConfig,
    root: Path,
    provenance: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[pd.DataFrame],
]:
    reports: list[
        dict[str, Any]
    ] = []

    predictions: list[
        pd.DataFrame
    ] = []

    for fold in folds:
        report, oof = _run_fold(
            fold=fold,
            dataset=dataset,
            eligible_index=(
                eligible_index
            ),
            factories=factories,
            configuration=configuration,
            returns=returns,
            ticker=ticker,
            raw_index=raw_index,
            config=config,
            root=root,
            provenance=provenance,
        )

        reports.append(report)
        predictions.append(oof)

    return reports, predictions


def _validate_oof(
    oof: pd.DataFrame,
) -> None:
    if not oof.index.is_unique:
        raise ValueError(
            "Duplicate or nonchronological OOF origins"
        )

    if not (
        oof.index.is_monotonic_increasing
    ):
        raise ValueError(
            "Duplicate or nonchronological OOF origins"
        )


def _build_summary(
    *,
    ticker: str,
    config: WalkForwardConfig,
    target_config: TargetConfig,
    raw: pd.DataFrame,
    dataset: Any,
    folds: list[Any],
    reports: list[
        dict[str, Any]
    ],
    oof: pd.DataFrame,
    configuration: dict[str, Any],
    provenance: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    return {
        "ticker": ticker.upper(),
        "mode": config.mode,
        "folds": len(folds),
        "raw_candles": len(raw),
        "feature_valid_rows": len(
            dataset.features.frame
        ),
        "aligned_rows": len(
            dataset.X
        ),
        "feature_count": len(
            dataset.features.feature_names
        ),
        "warmup_rows": (
            dataset.features.warmup_rows
        ),
        "oof_rows": len(oof),
        "oof_start": (
            oof.index[0].isoformat()
        ),
        "oof_end": (
            oof.index[-1].isoformat()
        ),
        "unused_trailing_labeled_origins": (
            len(raw)
            - target_config.horizon
            - folds[-1].test_end
        ),
        "models": aggregate_models(
            reports,
            oof,
            config,
        ),
        "training_prior_by_fold": [
            report["partitions"][
                "train"
            ]["class_balance"][
                "positive_ratio"
            ]
            for report in reports
        ],
        "configuration_fingerprint": (
            fingerprint(
                configuration
            )
        ),
        "provenance": provenance,
        "artifact_path": str(
            root.resolve()
        ),
    }


from stock_app.research.registry_workflows import registered_workflow


@registered_workflow
def run_walk_forward(
    history: pd.DataFrame,
    ticker: str,
    output: Path,
    config: WalkForwardConfig = (
        WalkForwardConfig()
    ),
    feature_config: FeatureConfig = (
        FeatureConfig()
    ),
    target_config: TargetConfig = (
        TargetConfig(task="binary")
    ),
    model_factories: (
        dict[
            str,
            ModelFactory,
        ]
        | None
    ) = None,
    eligible_index: (
        pd.DatetimeIndex | None
    ) = None,
    source: str = (
        "provided DataFrame"
    ),
    reference_artifact: (
        Path | None
    ) = None,
) -> dict[str, Any]:
    """Run chronological walk-forward evaluation.

    eligible_index permits predeclared matched-row
    ablations and must never be selected from outcomes.

    Each fold's test origins are unique. Forward-return
    label intervals may overlap and therefore are not
    independent trials.
    """
    if target_config.task != "binary":
        raise ValueError(
            "Stage 4 evaluates binary classification"
        )

    _validate_ticker(ticker)

    raw, raw_index = (
        _prepare_history(
            history,
            feature_config,
        )
    )

    folds = generate_folds(
        raw_index,
        target_config.horizon,
        config,
    )

    dataset = (
        build_feature_dataset(
            raw,
            feature_config,
            target_config,
            explicit_split=(
                folds[0].split
            ),
        )
    )

    returns = build_targets(
        raw["Close"],
        target_config,
    ).future_log_return

    factories = (
        default_factories()
        if model_factories is None
        else model_factories
    )

    if not factories:
        raise ValueError(
            "At least one model factory required"
        )

    invalid_model_name = any(
        not re.fullmatch(
            r"[a-z][a-z0-9_]*",
            name,
        )
        for name in factories
    )

    if invalid_model_name:
        raise ValueError(
            "Model names must be safe "
            "lowercase identifiers"
        )

    configuration = (
        _build_configuration(
            ticker=ticker,
            feature_config=(
                feature_config
            ),
            target_config=(
                target_config
            ),
            config=config,
            dataset=dataset,
            eligible_index=(
                eligible_index
            ),
        )
    )

    root = _create_run_root(
        output,
        ticker,
        feature_config.interval,
        target_config.horizon,
    )

    provenance = (
        _build_provenance(
            raw,
            raw_index,
            source,
        )
    )

    reports, predictions = (
        _run_all_folds(
            folds=folds,
            dataset=dataset,
            eligible_index=(
                eligible_index
            ),
            factories=factories,
            configuration=(
                configuration
            ),
            returns=returns,
            ticker=ticker,
            raw_index=raw_index,
            config=config,
            root=root,
            provenance=provenance,
        )
    )

    oof = pd.concat(
        predictions
    )

    _validate_oof(oof)

    if reference_artifact is not None:
        from .frozen_comparison import (
            merge_frozen_reference,
        )

        merge_frozen_reference(
            reference_artifact,
            raw,
            configuration,
            reports,
            oof,
        )

    summary = _build_summary(
        ticker=ticker,
        config=config,
        target_config=target_config,
        raw=raw,
        dataset=dataset,
        folds=folds,
        reports=reports,
        oof=oof,
        configuration=configuration,
        provenance=provenance,
        root=root,
    )

    market = raw.copy()

    market.insert(
        0,
        "raw_position",
        np.arange(len(raw)),
    )

    write_artifacts(
        root,
        configuration,
        summary,
        reports,
        aggregate_importance(
            reports
        ),
        oof,
        market,
    )

    return summary


def _restore_reference_timezone(
    raw: pd.DataFrame,
    timezone_name: str | None,
) -> pd.DataFrame:
    restored = raw.copy()

    index = pd.DatetimeIndex(
        restored.index
    )

    if index.tz is not None:
        restored.index = (
            index.tz_convert(
                timezone_name
            )
        )

    elif timezone_name is not None:
        restored.index = (
            index.tz_localize(
                timezone_name
            )
        )

    else:
        restored.index = index

    return restored


def _load_input_data(
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, str]:
    if args.reference_artifact:
        from ..backtest.inputs import (
            load_frozen_inputs,
        )

        if args.csv:
            raise ValueError(
                "Use either a frozen "
                "reference snapshot or CSV"
            )

        reference = (
            load_frozen_inputs(
                args.reference_artifact,
                ticker=args.ticker,
            )
        )

        expected_target = asdict(
            TargetConfig(
                horizon=args.horizon,
                threshold=(
                    args.target_threshold
                ),
                task="binary",
            )
        )

        if (
            reference.configuration[
                "target"
            ]
            != expected_target
        ):
            raise ValueError(
                "Reference target/"
                "interval/mode mismatch"
            )

        if (
            reference.configuration[
                "interval"
            ]
            != args.interval
        ):
            raise ValueError(
                "Reference target/"
                "interval/mode mismatch"
            )

        if (
            reference.summary["mode"]
            != args.mode
        ):
            raise ValueError(
                "Reference target/"
                "interval/mode mismatch"
            )

        raw = (
            _restore_reference_timezone(
                reference.market,
                reference.summary[
                    "provenance"
                ][
                    "index_timezone"
                ],
            )
        )

        source = (
            "Yahoo daily full-history "
            "cache; frozen Stage 4 "
            "reference snapshot"
        )

        return raw, source

    if args.csv:
        return (
            pd.read_csv(args.csv),
            str(
                args.csv.resolve()
            ),
        )

    if args.interval != "1d":
        raise ValueError(
            "Use --csv for intraday research"
        )

    from ..stock_service import (
        _get_full_history,
    )

    return (
        _get_full_history(
            args.ticker
        ),
        "Yahoo daily full-history cache",
    )


def _selected_factories(
    model_names: str,
) -> dict[str, ModelFactory]:
    factories = default_factories()

    names = [
        name.strip()
        for name in model_names.split(",")
        if name.strip()
    ]

    if "gru" in names:
        from ..models.gru_model import (
            GRUClassifier,
        )

        factories["gru"] = (
            GRUClassifier
        )

    unknown_models = [
        name
        for name in names
        if name not in factories
    ]

    if unknown_models:
        raise ValueError(
            "Unknown model selection"
        )

    return {
        name: factories[name]
        for name in names
    }


def _build_parser(
) -> argparse.ArgumentParser:
    defaults = (
        WalkForwardConfig()
    )

    parser = (
        argparse.ArgumentParser(
            description=__doc__,
        )
    )

    parser.add_argument(
        "--ticker",
        required=True,
    )

    parser.add_argument(
        "--interval",
        default="1d",
    )

    parser.add_argument(
        "--horizon",
        type=int,
        default=(
            TargetConfig().horizon
        ),
    )

    parser.add_argument(
        "--target-threshold",
        type=float,
        default=(
            TargetConfig().threshold
        ),
    )

    parser.add_argument(
        "--mode",
        choices=[
            "expanding",
            "rolling",
        ],
        default=defaults.mode,
    )

    parser.add_argument(
        "--train-size",
        type=int,
        default=(
            defaults
            .minimum_train_samples
        ),
    )

    parser.add_argument(
        "--rolling-train-size",
        type=int,
        default=(
            defaults
            .rolling_train_samples
        ),
    )

    parser.add_argument(
        "--validation-size",
        type=int,
        default=(
            defaults
            .validation_samples
        ),
    )

    parser.add_argument(
        "--test-size",
        type=int,
        default=(
            defaults
            .test_samples
        ),
    )

    parser.add_argument(
        "--step-size",
        type=int,
        default=(
            defaults
            .step_samples
        ),
    )

    parser.add_argument(
        "--max-folds",
        type=int,
    )

    parser.add_argument(
        "--csv",
        type=Path,
    )

    parser.add_argument(
        "--models",
        default=(
            "majority,"
            "training_prior,"
            "momentum,"
            "logistic,"
            "xgboost"
        ),
    )

    parser.add_argument(
        "--reference-artifact",
        type=Path,
        help=(
            "Matched frozen baseline run; "
            "use its snapshot via CSV/API"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "artifacts"
        ),
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(levelname)s %(message)s"
        ),
    )

    try:
        config = WalkForwardConfig(
            args.mode,
            args.train_size,
            args.validation_size,
            args.test_size,
            args.step_size,
            args.rolling_train_size,
            args.max_folds,
        )

        raw, source = (
            _load_input_data(args)
        )

        factories = (
            _selected_factories(
                args.models
            )
        )

        summary = run_walk_forward(
            raw,
            args.ticker,
            args.output,
            config,
            FeatureConfig(
                interval=args.interval
            ),
            TargetConfig(
                horizon=args.horizon,
                threshold=(
                    args.target_threshold
                ),
                task="binary",
            ),
            model_factories=(
                factories
            ),
            source=source,
            reference_artifact=(
                args.reference_artifact
            ),
        )

    except (
        ValueError,
        OSError,
        ImportError,
    ) as exc:
        parser.exit(
            1,
            (
                "Walk-forward failed: "
                f"{exc}\n"
            ),
        )

    print(
        json.dumps(
            {
                "artifact_path": (
                    summary[
                        "artifact_path"
                    ]
                ),
                "folds": (
                    summary["folds"]
                ),
                "models": {
                    name: {
                        "mean_auc": (
                            result[
                                "aggregate"
                            ]["roc_auc"]
                        ),
                        "oof": (
                            result["oof"]
                        ),
                    }
                    for name, result
                    in summary[
                        "models"
                    ].items()
                },
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()