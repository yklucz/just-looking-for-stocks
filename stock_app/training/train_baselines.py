"""Offline, single-split binary research. Never called by normal Flask inference."""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import random
import re
import subprocess
from time import perf_counter
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from ..config import (
    DEFAULT_SPLIT,
    FeatureConfig,
    LogisticConfig,
    TargetConfig,
    XGBoostConfig,
)
from ..features.validation import validate_history
from ..models import (
    LogisticClassifier,
    MajorityClassifier,
    MomentumClassifier,
    PriorClassifier,
    XGBoostClassifier,
)
from ..models.base import class_balance
from ..models.registry import model_contract, save_model
from ..targets.target_builder import build_targets
from .feature_dataset import build_feature_dataset
from .model_inputs import evaluate_partitioned, fit_partitioned


logger = logging.getLogger(__name__)


def feature_preset(
    name: str,
    interval: str = "1d",
) -> FeatureConfig:
    config = FeatureConfig(interval=interval)

    if name == "all":
        return config

    if name in {"without_time", "without_volume"}:
        return replace(
            config,
            **{
                name.removeprefix("without_"): False,
            },
        )

    if name in {"returns_only", "trend_only"}:
        selected_group = name.removesuffix("_only")

        return replace(
            config,
            **{
                group: group == selected_group
                for group in (
                    "returns",
                    "trend",
                    "momentum",
                    "volatility",
                    "candle",
                    "volume",
                    "time",
                )
            },
        )

    raise ValueError("Unknown feature preset")


def runtime_provenance() -> dict[str, Any]:
    versions = {
        name: importlib.metadata.version(name)
        for name in (
            "numpy",
            "pandas",
            "scikit-learn",
            "xgboost",
        )
    }

    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    except (OSError, subprocess.CalledProcessError):
        revision = None
        dirty = None

    return {
        "versions": versions,
        "git_revision": revision,
        "git_dirty": dirty,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def _hash_dataframe(frame: pd.DataFrame) -> str:
    """Return a stable SHA-256 hash for a pandas DataFrame."""
    hashed = pd.util.hash_pandas_object(
        frame,
        index=True,
    )

    payload = hashed.to_numpy(
        dtype=np.uint64,
        copy=False,
    ).tobytes()

    return hashlib.sha256(payload).hexdigest()


from stock_app.research.registry_workflows import registered_workflow


@registered_workflow
def run_research(
    history: pd.DataFrame,
    ticker: str,
    output: Path,
    feature_config: FeatureConfig = FeatureConfig(),
    target_config: TargetConfig = TargetConfig(task="binary"),
    logistic_config: LogisticConfig = LogisticConfig(),
    xgboost_config: XGBoostConfig = XGBoostConfig(),
    decision_threshold: float = 0.5,
    source: str = "provided DataFrame",
    additional_factories: tuple = (),
) -> dict[str, Any]:
    if target_config.task != "binary":
        raise ValueError(
            "Stage 3 runner supports binary classification only"
        )

    if (
        not re.fullmatch(
            r"[A-Za-z0-9.^=_-]{1,32}",
            ticker,
        )
        or ticker in {".", ".."}
    ):
        raise ValueError("Invalid ticker identifier")

    if not 0 < decision_threshold < 1:
        raise ValueError(
            "Decision probability threshold must be in (0, 1)"
        )

    random.seed(logistic_config.random_state)
    np.random.seed(logistic_config.random_state)

    raw = validate_history(
        history,
        feature_config,
    )

    dataset = build_feature_dataset(
        raw,
        feature_config,
        target_config,
    )

    x_train, _ = dataset.partition("train")
    x_val, _ = dataset.partition("validation")

    candidates = [
        MajorityClassifier(),
        PriorClassifier(),
    ]

    skipped: dict[str, str] = {}

    if "return_1" in x_train:
        candidates.append(MomentumClassifier())
    else:
        skipped["momentum"] = (
            "Feature preset omits return_1"
        )

    candidates += [
        LogisticClassifier(logistic_config),
        XGBoostClassifier(xgboost_config),
    ]

    candidates += [
        factory()
        for factory in additional_factories
    ]

    returns = build_targets(
        raw["Close"],
        target_config,
    ).future_log_return

    results: dict[str, Any] = {}
    contracts: dict[str, Any] = {}

    for model in candidates:
        logger.info(
            "Training %s: train=%d validation=%d",
            model.model_type,
            len(x_train),
            len(x_val),
        )

        started = perf_counter()

        fit_partitioned(
            model,
            dataset,
        )

        results[model.model_type] = {
            "fit_seconds": perf_counter() - started,
        }

        for partition_name in (
            "train",
            "validation",
        ):
            results[model.model_type][
                partition_name
            ] = evaluate_partitioned(
                model,
                dataset,
                partition_name,
                returns,
                decision_threshold,
            )

        if hasattr(
            model,
            "training_diagnostics",
        ):
            results[model.model_type][
                "training_diagnostics"
            ] = model.training_diagnostics

        contracts[model.model_type] = (
            model_contract(
                model,
                dataset,
                ticker,
                asdict(DEFAULT_SPLIT),
            )
        )

    # All candidates are fitted and frozen before test metrics are computed.
    for model in candidates:
        results[model.model_type][
            "test"
        ] = evaluate_partitioned(
            model,
            dataset,
            "test",
            returns,
            decision_threshold,
        )

    partitions: dict[str, Any] = {}

    for partition_name in (
        "train",
        "validation",
        "test",
    ):
        x_part, y_part = dataset.partition(
            partition_name
        )

        partitions[partition_name] = {
            "samples": len(x_part),
            "origin_start": (
                x_part.index[0].isoformat()
            ),
            "origin_end": (
                x_part.index[-1].isoformat()
            ),
            "target_end": (
                dataset.target_times.loc[
                    x_part.index[-1]
                ].isoformat()
            ),
            "class_balance": (
                class_balance(y_part)
            ),
        }

    xgb = next(
        model
        for model in candidates
        if isinstance(
            model,
            XGBoostClassifier,
        )
    )

    provenance = runtime_provenance()

    provenance.update(
        source=source,
        raw_data_sha256=_hash_dataframe(raw),
    )

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
        / feature_config.interval
        / f"h{target_config.horizon}"
        / run_id
    )

    report: dict[str, Any] = {
        "ticker": ticker.upper(),
        "source": source,
        "raw_candles": len(raw),
        "feature_valid_rows": len(
            dataset.features.frame
        ),
        "aligned_rows": len(dataset.X),
        "warmup_rows": (
            dataset.features.warmup_rows
        ),
        "feature_count": len(
            dataset.features.feature_names
        ),
        "features": dataset.features.metadata,
        "target": asdict(target_config),
        "decision_threshold": decision_threshold,
        "split_config": asdict(DEFAULT_SPLIT),
        "partitions": partitions,
        "models": results,
        "skipped_models": skipped,
        "xgboost_best_iteration": int(
            xgb.estimator.best_iteration
        ),
        "xgboost_scale_pos_weight": (
            xgb.scale_pos_weight
        ),
        "xgboost_gain_importance": (
            xgb.feature_importance()
        ),
        "provenance": provenance,
        "report_path": str(
            (root / "report.json").resolve()
        ),
        "artifacts": {},
    }

    root.mkdir(
        parents=True,
        exist_ok=True,
    )

    for model in candidates:
        path = save_model(
            model,
            root / model.model_type,
            contracts[model.model_type],
            results[model.model_type],
            {
                **provenance,
                "decision_threshold": (
                    decision_threshold
                ),
                "partitions": partitions,
            },
        )

        report["artifacts"][
            model.model_type
        ] = str(path.resolve())

    (root / "report.json").write_text(
        json.dumps(
            report,
            indent=2,
            allow_nan=False,
        )
    )

    logger.info(
        "Research report saved to %s",
        root / "report.json",
    )

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
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
        default=TargetConfig().horizon,
    )

    parser.add_argument(
        "--target-threshold",
        type=float,
        default=TargetConfig().threshold,
    )

    parser.add_argument(
        "--decision-threshold",
        type=float,
        default=0.5,
    )

    parser.add_argument(
        "--features",
        choices=[
            "all",
            "without_time",
            "without_volume",
            "returns_only",
            "trend_only",
        ],
        default="all",
    )

    parser.add_argument(
        "--csv",
        type=Path,
        help=(
            "Offline OHLCV with a "
            "Date/Datetime column"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts"),
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
    )

    try:
        config = feature_preset(
            args.features,
            args.interval,
        )

        if args.csv:
            history = pd.read_csv(
                args.csv
            )

            source = (
                f"CSV: {args.csv.resolve()}"
            )

        else:
            if args.interval != "1d":
                raise ValueError(
                    "Yahoo research entrypoint uses "
                    "daily full-history cache; "
                    "provide --csv for other intervals"
                )

            from ..stock_service import (
                _get_full_history,
            )

            logger.info(
                "Retrieving %s daily Yahoo history once",
                args.ticker,
            )

            history = _get_full_history(
                args.ticker
            )

            source = (
                "Yahoo Finance via existing "
                "daily history cache"
            )

        report = run_research(
            history,
            args.ticker,
            args.output,
            config,
            TargetConfig(
                horizon=args.horizon,
                threshold=args.target_threshold,
                task="binary",
            ),
            decision_threshold=(
                args.decision_threshold
            ),
            source=source,
        )

    except (
        ValueError,
        OSError,
        ImportError,
    ) as exc:
        parser.exit(
            1,
            f"Research failed: {exc}\n",
        )

    print(
        json.dumps(
            {
                "report_path": report[
                    "report_path"
                ],
                "partitions": report[
                    "partitions"
                ],
                "validation": {
                    model_name: result[
                        "validation"
                    ]
                    for model_name, result
                    in report["models"].items()
                },
                "test": {
                    model_name: result["test"]
                    for model_name, result
                    in report["models"].items()
                },
            },
            indent=2,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()