"""Small explicit artifact contracts; trusted local files only (joblib executes code)."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from ..config import XGBoostConfig
from .xgboost_model import XGBoostClassifier
from .return_model import XGBoostReturnRegressor


def canonical(value: Any) -> dict[str, Any]:
    return json.loads(
        json.dumps(
            value,
            sort_keys=True,
            allow_nan=False,
        )
    )


def fingerprint(contract: dict[str, Any]) -> str:
    payload = json.dumps(
        contract,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()

    return hashlib.sha256(payload).hexdigest()


def _pandas_hash_bytes(value: Any) -> bytes:
    """Convert a pandas hash result into raw bytes safely."""
    hashed = pd.util.hash_pandas_object(
        value,
        index=True,
    )

    return hashed.to_numpy(
        dtype=np.uint64,
        copy=False,
    ).tobytes()


def _update_digest(
    digest: Any,
    value: Any,
) -> None:
    digest.update(
        _pandas_hash_bytes(value)
    )


def _partition_contract_data(
    model: Any,
    dataset: Any,
    partition: str,
    digest: Any,
) -> tuple[Any, Any]:
    x_data, y_data = dataset.partition(
        partition
    )

    if not getattr(
        model,
        "sequential",
        False,
    ):
        return x_data, y_data

    from ..training.model_inputs import model_partition

    sequences, y_data = model_partition(
        model,
        dataset,
        partition,
    )

    x_data = x_data.loc[
        sequences.index
    ]

    context_start = (
        sequences.positions[0]
        - model.sequence_length
        + 1
    )

    context_end = (
        sequences.positions[-1]
        + 1
    )

    context = sequences.history.iloc[
        context_start:context_end
    ]

    _update_digest(
        digest,
        context,
    )

    if sequences.scaler_rows is not None:
        _update_digest(
            digest,
            sequences.scaler_rows,
        )

    return x_data, y_data


def model_contract(
    model: Any,
    dataset: Any,
    ticker: str,
    split_config: dict[str, Any],
) -> dict[str, Any]:
    digest = hashlib.sha256()

    dates: dict[str, Any] = {}

    for partition in (
        "train",
        "validation",
    ):
        x_data, y_data = _partition_contract_data(
            model,
            dataset,
            partition,
            digest,
        )

        target_times = dataset.target_times.loc[
            x_data.index
        ]

        for values in (
            x_data,
            y_data,
            target_times,
        ):
            _update_digest(
                digest,
                values,
            )

        dates[partition] = {
            "origin_start": (
                x_data.index[0].isoformat()
            ),
            "origin_end": (
                x_data.index[-1].isoformat()
            ),
            "label_end": (
                dataset.target_times.loc[
                    x_data.index[-1]
                ].isoformat()
            ),
            "samples": len(x_data),
        }

    if hasattr(
        model,
        "config",
    ):
        model_parameters = asdict(
            model.config
        )
    else:
        model_parameters = {}

    contract: dict[str, Any] = {
        "version": "research-v1",
        "model_type": model.model_type,
        "ticker": ticker.upper(),
        "interval": (
            dataset.features.config.interval
        ),
        "feature_names": list(
            dataset.features.feature_names
        ),
        "feature_config": asdict(
            dataset.features.config
        ),
        "feature_version": (
            dataset.features.metadata[
                "version"
            ]
        ),
        "target": asdict(
            dataset.target_config
        ),
        "split_config": split_config,
        "history_start": (
            dataset.features
            .all_features
            .index[0]
            .isoformat()
        ),
        "fit_data_sha256": (
            digest.hexdigest()
        ),
        "fit_dates": dates,
        "model_parameters": model_parameters,
    }

    if getattr(
        model,
        "sequential",
        False,
    ):
        contract["sequence_length"] = (
            model.sequence_length
        )

        contract["scaler_fit"] = (
            model.scaler_fit_dates
        )

    return canonical(
        contract
    )


def _save_filename(
    model: Any,
    native_xgboost: bool,
) -> str:
    if model.model_type == "gru":
        return "gru.pt"

    if native_xgboost:
        return "model.json"

    return "model.joblib"


def _load_filename(
    model_type: str,
) -> str:
    if model_type == "gru":
        return "gru.pt"

    if model_type in {"xgboost", "xgboost_regressor"}:
        return "model.json"

    return "model.joblib"


def _validate_model_contract(
    model: Any,
    contract: dict[str, Any],
) -> None:
    if (
        list(model.feature_names)
        != contract["feature_names"]
        or model.model_type
        != contract["model_type"]
    ):
        raise ValueError(
            "Model does not match artifact contract"
        )

    if not hasattr(
        model,
        "config",
    ):
        return

    parameters = canonical(
        asdict(model.config)
    )

    if (
        parameters
        != contract["model_parameters"]
    ):
        raise ValueError(
            "Model parameters do not match artifact contract"
        )


def _save_model_payload(
    model: Any,
    path: Path,
    filename: str,
    native_xgboost: bool,
) -> list[str]:
    if model.model_type == "gru":
        result = model.save_native(
            path
        )

        return list(
            result["extra_files"]
        )

    if native_xgboost:
        model.estimator.save_model(
            path / filename
        )

        return []

    joblib.dump(
        model,
        path / filename,
    )

    return []


def _extra_checksums(
    path: Path,
    files: list[str],
) -> dict[str, str]:
    return {
        name: hashlib.sha256(
            (path / name).read_bytes()
        ).hexdigest()
        for name in files
    }


def save_model(
    model: Any,
    path: Path,
    contract: dict[str, Any],
    metrics: dict[str, Any],
    provenance: dict[str, Any] | None = None,
) -> Path:
    contract = canonical(
        contract
    )

    _validate_model_contract(
        model,
        contract,
    )

    path = Path(path)

    path.mkdir(
        parents=True,
        exist_ok=False,
    )

    native_xgboost = isinstance(
        model,
        (XGBoostClassifier, XGBoostReturnRegressor),
    )

    filename = _save_filename(
        model,
        native_xgboost,
    )

    extra_files = _save_model_payload(
        model,
        path,
        filename,
        native_xgboost,
    )

    metadata: dict[str, Any] = {
        "contract": contract,
        "fingerprint": fingerprint(
            contract
        ),
        "model_file": filename,
        "model_sha256": (
            hashlib.sha256(
                (path / filename).read_bytes()
            ).hexdigest()
        ),
        "metrics": metrics,
        "provenance": (
            provenance or {}
        ),
    }

    if extra_files:
        metadata["extra_sha256"] = (
            _extra_checksums(
                path,
                extra_files,
            )
        )

    (
        path
        / "feature_names.json"
    ).write_text(
        json.dumps(
            contract["feature_names"],
            indent=2,
        )
    )

    (
        path
        / "metadata.json"
    ).write_text(
        json.dumps(
            metadata,
            indent=2,
            allow_nan=False,
        )
    )

    return path


def _load_and_validate_metadata(
    path: Path,
    expected_contract: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[str],
    str,
]:
    metadata: dict[str, Any] = json.loads(
        (
            path
            / "metadata.json"
        ).read_text()
    )

    expected = canonical(
        expected_contract
    )

    expected_fingerprint = fingerprint(
        expected
    )

    if (
        metadata["contract"]
        != expected
        or metadata["fingerprint"]
        != expected_fingerprint
    ):
        raise ValueError(
            "Artifact feature/target/config fingerprint mismatch"
        )

    names: list[str] = json.loads(
        (
            path
            / "feature_names.json"
        ).read_text()
    )

    if (
        names
        != expected["feature_names"]
    ):
        raise ValueError(
            "Artifact feature order mismatch"
        )

    filename = _load_filename(
        expected["model_type"]
    )

    if (
        metadata["model_file"]
        != filename
    ):
        raise ValueError(
            "Unexpected model file"
        )

    return (
        metadata,
        expected,
        names,
        filename,
    )


def _verify_model_checksum(
    path: Path,
    filename: str,
    metadata: dict[str, Any],
) -> None:
    checksum = hashlib.sha256(
        (
            path
            / filename
        ).read_bytes()
    ).hexdigest()

    if (
        checksum
        != metadata["model_sha256"]
    ):
        raise ValueError(
            "Model file checksum mismatch"
        )


def _verify_gru_files(
    path: Path,
    metadata: dict[str, Any],
) -> None:
    required_files = {
        "scaler.pkl",
        "configuration.json",
        "training_history.json",
    }

    checksums = metadata.get(
        "extra_sha256",
        {},
    )

    if (
        set(checksums)
        != required_files
    ):
        raise ValueError(
            "Incomplete GRU artifact checksums"
        )

    for name, expected_checksum in checksums.items():
        checksum = hashlib.sha256(
            (
                path
                / name
            ).read_bytes()
        ).hexdigest()

        if (
            checksum
            != expected_checksum
        ):
            raise ValueError(
                "GRU auxiliary file checksum mismatch"
            )


def _load_gru_model(
    path: Path,
    expected: dict[str, Any],
    metadata: dict[str, Any],
    device: str,
) -> Any:
    from .gru_model import GRUClassifier

    _verify_gru_files(
        path,
        metadata,
    )

    return GRUClassifier.load_native(
        path,
        expected,
        device=device,
    )


def _load_xgboost_model(
    path: Path,
    filename: str,
    expected: dict[str, Any],
) -> XGBoostClassifier | XGBoostReturnRegressor:
    from threadpoolctl import threadpool_limits
    from xgboost import XGBClassifier, XGBRegressor

    regression = expected['model_type'] == 'xgboost_regressor'
    config = XGBoostConfig(
        **expected[
            "model_parameters"
        ]
    )

    if regression:
        model = XGBoostReturnRegressor(config)
        model.estimator = XGBRegressor(n_jobs=model.config.n_jobs)
    else:
        model = XGBoostClassifier(config)
        model.estimator = XGBClassifier(n_jobs=model.config.n_jobs)
        model.scale_pos_weight = model.estimator.get_params().get("scale_pos_weight")

    # Native loading can use the global
    # OpenMP pool before saved parameters apply.
    with threadpool_limits(
        limits=model.config.n_jobs,
        user_api="openmp",
    ):
        model.estimator.load_model(
            path / filename
        )

    booster_feature_names = (
        model.estimator
        .get_booster()
        .feature_names
    )

    # XGBoost types feature_names as
    # FeatureNames | None.
    if booster_feature_names is None:
        raise ValueError(
            "XGBoost artifact does not contain feature names"
        )

    model.feature_names = tuple(
        str(name)
        for name in booster_feature_names
    )

    return model


def _deserialize_model(
    path: Path,
    filename: str,
    expected: dict[str, Any],
    metadata: dict[str, Any],
    device: str,
) -> Any:
    model_type = expected[
        "model_type"
    ]

    if model_type == "gru":
        return _load_gru_model(
            path,
            expected,
            metadata,
            device,
        )

    if model_type in {"xgboost", "xgboost_regressor"}:
        return _load_xgboost_model(
            path,
            filename,
            expected,
        )

    return joblib.load(
        path / filename
    )


def _validate_loaded_model(
    model: Any,
    expected: dict[str, Any],
    names: list[str],
) -> None:
    if (
        model.model_type
        != expected["model_type"]
        or list(model.feature_names)
        != names
    ):
        raise ValueError(
            "Loaded model schema mismatch"
        )


def load_model(
    path: Path,
    expected_contract: dict[str, Any],
    *,
    device: str = "auto",
) -> Any:
    """Validate caller's contract before deserializing."""
    path = Path(path)

    (
        metadata,
        expected,
        names,
        filename,
    ) = _load_and_validate_metadata(
        path,
        expected_contract,
    )

    _verify_model_checksum(
        path,
        filename,
        metadata,
    )

    model = _deserialize_model(
        path,
        filename,
        expected,
        metadata,
        device,
    )

    _validate_loaded_model(
        model,
        expected,
        names,
    )

    return model
