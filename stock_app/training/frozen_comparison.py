"""Attach unchanged frozen baseline probabilities on exactly matched test origins."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from ..backtest.inputs import load_frozen_inputs


def _validate_contract(
    configuration: dict[str, Any],
    reference_configuration: dict[str, Any],
) -> None:
    """Ensure the current run matches the frozen feature/target contract."""
    matches = (
        configuration["ticker"] == reference_configuration["ticker"]
        and configuration["target"] == reference_configuration["target"]
        and configuration["features"]["feature_names"]
        == reference_configuration["features"]["feature_names"]
        and configuration["features"]["feature_config"]
        == reference_configuration["features"]["feature_config"]
    )

    if not matches:
        raise ValueError("Frozen feature/target contract mismatch")


def _validate_market_snapshot(
    raw: pd.DataFrame,
    reference_market: pd.DataFrame,
) -> None:
    """Verify that the raw market data exactly matches the frozen snapshot."""
    normalized = raw.copy()

    # Explicit DatetimeIndex fixes Pylance's Index[Any] type issue.
    raw_index = pd.DatetimeIndex(normalized.index)

    normalized.index = raw_index.tz_convert("UTC")

    try:
        assert_frame_equal(
            normalized,
            reference_market,
            check_freq=False,
        )
    except AssertionError as exc:
        raise ValueError("Frozen market snapshot differs") from exc


def _align_frozen_oof(
    frozen_oof: pd.DataFrame,
    oof: pd.DataFrame,
) -> pd.DataFrame:
    """Align frozen OOF rows to the current run's exact test origins."""
    frozen = frozen_oof.copy()

    # Make datetime index types explicit for Pylance.
    frozen_index = pd.DatetimeIndex(frozen.index)
    oof_index = pd.DatetimeIndex(oof.index)

    frozen.index = frozen_index.tz_convert(oof_index.tz)

    if not oof_index.isin(frozen.index).all():
        raise ValueError("No exact frozen OOF match")

    return frozen.loc[oof_index]


def _validate_oof_alignment(
    oof: pd.DataFrame,
    aligned: pd.DataFrame,
) -> None:
    """Verify identity fields between current and frozen OOF rows."""
    for field in (
        "fold_id",
        "raw_position",
        "actual_target",
        "future_log_return",
    ):
        if not np.allclose(
            oof[field],
            aligned[field],
            rtol=1e-12,
            atol=1e-14,
        ):
            raise ValueError("Frozen OOF alignment mismatch")


def _load_old_folds(
    path: Path,
) -> dict[Any, dict[str, Any]]:
    """Load frozen fold reports keyed by fold ID."""
    fold_data = json.loads(
        (path / "folds.json").read_text()
    )

    return {
        fold["fold_id"]: fold
        for fold in fold_data
    }


def _merge_frozen_fold_models(
    path: Path,
    reports: list[dict[str, Any]],
    old_folds: dict[Any, dict[str, Any]],
) -> None:
    """Attach frozen model metrics and artifacts to each current fold."""
    for fold in reports:
        old = old_folds[fold["fold_id"]]

        if fold["raw_boundaries"] != old["raw_boundaries"]:
            raise ValueError("Frozen fold boundaries differ")

        for name, metrics in old["models"].items():
            if name in fold["models"]:
                raise ValueError(
                    "Cannot overwrite a frozen reference model"
                )

            scores = deepcopy(metrics)

            scores["artifact"] = str(
                path / scores["artifact"]
            )

            scores["frozen_reference"] = True

            fold["models"][name] = scores

        fold["xgboost_importance"] = old.get(
            "xgboost_importance",
            [],
        )


def _copy_frozen_predictions(
    model_names: Any,
    aligned: pd.DataFrame,
    oof: pd.DataFrame,
) -> None:
    """Copy frozen probabilities and predictions into the current OOF frame."""
    for model in model_names:
        for suffix in (
            "probability",
            "prediction",
        ):
            column = f"{model}_{suffix}"

            oof[column] = aligned[
                column
            ].to_numpy()


def merge_frozen_reference(
    path: Path,
    raw: pd.DataFrame,
    configuration: dict[str, Any],
    reports: list[dict[str, Any]],
    oof: pd.DataFrame,
) -> None:
    """Merge a frozen reference run into current walk-forward results."""
    reference = load_frozen_inputs(path)

    reference_configuration = (
        reference.configuration
    )

    _validate_contract(
        configuration,
        reference_configuration,
    )

    _validate_market_snapshot(
        raw,
        reference.market,
    )

    aligned = _align_frozen_oof(
        reference.oof,
        oof,
    )

    _validate_oof_alignment(
        oof,
        aligned,
    )

    old_folds = _load_old_folds(
        path
    )

    _merge_frozen_fold_models(
        path,
        reports,
        old_folds,
    )

    _copy_frozen_predictions(
        reference_configuration["models"],
        aligned,
        oof,
    )

    configuration["models"].update(
        reference_configuration["models"]
    )

    configuration["frozen_reference"] = {
        "path": str(reference.path),
        "manifest_sha256": (
            reference.source_manifest_sha256
        ),
        "configuration_fingerprint": (
            reference.summary[
                "configuration_fingerprint"
            ]
        ),
        "matched_oof_rows": len(oof),
    }