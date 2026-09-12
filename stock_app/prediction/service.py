"""Daily, completed-candle binary probabilities from saved XGBoost only."""

from datetime import datetime, timezone
import logging
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd

from ..config import FeatureConfig, PredictionConfig
from ..features import build_features
from ..features.validation import validate_history
from ..features.numerics import exceeds_price_bound
from .artifact_loader import load_bound_model
from .schemas import PredictionError


logger = logging.getLogger(__name__)


def load_history(ticker: str) -> pd.DataFrame:
    from ..stock_service import _get_full_history

    return _get_full_history(ticker)


def _trim_invalid_trailing_rows(
    history: pd.DataFrame,
) -> pd.DataFrame:
    """Drop unusable trailing provider rows.

    Invalid data in the middle of history is rejected.
    """
    required = {
        "Open",
        "High",
        "Low",
        "Close",
    }

    if not required.issubset(
        history.columns
    ):
        return history

    columns = [
        "Open",
        "High",
        "Low",
        "Close",
    ]

    if "Volume" in history:
        columns.append(
            "Volume"
        )

    values = (
        history.loc[:, columns]
        .apply(
            pd.to_numeric,
            errors="coerce",
        )
        .to_numpy(
            dtype=float
        )
    )

    invalid = ~np.isfinite(
        values
    ).all(axis=1)

    prices = values[:, :4]

    invalid |= (
        prices <= 0
    ).any(axis=1)

    # Low > High
    invalid |= (
        exceeds_price_bound(prices[:, 2], prices[:, 1])
    )

    # Open < Low
    invalid |= (
        exceeds_price_bound(prices[:, 2], prices[:, 0])
    )

    # Open > High
    invalid |= (
        exceeds_price_bound(prices[:, 0], prices[:, 1])
    )

    # Close < Low
    invalid |= (
        exceeds_price_bound(prices[:, 2], prices[:, 3])
    )

    # Close > High
    invalid |= (
        exceeds_price_bound(prices[:, 3], prices[:, 1])
    )

    if "Volume" in history:
        invalid |= (
            values[:, 4] < 0
        )

    bad_positions = np.flatnonzero(
        invalid
    )

    if not len(
        bad_positions
    ):
        return history

    first_bad = int(
        bad_positions[0]
    )

    if (
        bad_positions[-1]
        != len(history) - 1
        or not invalid[
            first_bad:
        ].all()
    ):
        row = history.iloc[first_bad]
        details = ', '.join(f'{name}={row[name]!r}' for name in columns)
        raise ValueError(
            f"Invalid OHLC in historical context at {history.index[first_bad]}: {details}"
        )

    trimmed = history.iloc[
        :first_bad
    ]

    if trimmed.empty:
        raise ValueError(
            "No valid completed market candles remain"
        )

    logger.warning(
        "Ignoring %d invalid trailing market candle(s)",
        len(history)
        - len(trimmed),
    )

    return trimmed


def _validate_ticker(
    ticker: str,
) -> str:
    normalized = ticker.upper()

    if not re.fullmatch(
        r"[A-Z0-9.^=_-]{1,32}",
        normalized,
    ):
        raise PredictionError(
            "INVALID_REQUEST",
            "Invalid ticker",
            400,
        )

    return normalized


def _prediction_clock(
    now: datetime | None,
) -> pd.Timestamp:
    clock = pd.Timestamp(
        now
        or datetime.now(
            timezone.utc
        )
    )

    if clock.tzinfo is None:
        raise ValueError(
            "Clock must be timezone aware"
        )

    return clock


def _validate_history_index(
    history: pd.DataFrame,
) -> pd.DatetimeIndex:
    if not isinstance(
        history.index,
        pd.DatetimeIndex,
    ):
        raise ValueError(
            "Chronological unique market timestamps required"
        )

    index = pd.DatetimeIndex(
        history.index
    )

    if (
        index.hasnans
        or not index.is_unique
        or not index.is_monotonic_increasing
    ):
        raise ValueError(
            "Chronological unique market timestamps required"
        )

    return index


def _completed_history(
    ticker: str,
    clock: pd.Timestamp,
) -> tuple[
    pd.DataFrame,
    pd.Timestamp,
]:
    history = load_history(
        ticker
    )

    index = (
        _validate_history_index(
            history
        )
    )

    # Exclude today's candle even after
    # market close. This keeps daily
    # prediction availability conservative.
    local_clock = (
        clock.tz_convert(
            index.tz
            or "UTC"
        )
    )

    completed_mask = (
        index.date
        < local_clock.date()
    )

    history = history.loc[
        completed_mask
    ]

    history = (
        _trim_invalid_trailing_rows(
            history
        )
    )

    history = validate_history(
        history,
        FeatureConfig(),
    )

    return (
        history,
        local_clock,
    )


def _restrict_to_artifact_history(
    history: pd.DataFrame,
    contract: dict[str, Any],
) -> pd.DataFrame:
    start = pd.Timestamp(
        contract[
            "history_start"
        ]
    )

    normalized = pd.to_datetime(
        history.index,
        utc=True,
    )

    start_utc = pd.to_datetime(
        start,
        utc=True,
    )

    history = history.loc[
        normalized
        >= start_utc
    ]

    if history.empty:
        raise ValueError(
            "Full historical context from the artifact start is required"
        )

    first_timestamp = (
        pd.to_datetime(
            history.index[0],
            utc=True,
        )
    )

    if first_timestamp != start_utc:
        raise ValueError(
            "Full historical context from the artifact start is required"
        )

    return history


def _validate_features(
    history: pd.DataFrame,
    contract: dict[str, Any],
) -> Any:
    features = build_features(
        history
    )

    if (
        list(
            features.feature_names
        )
        != contract[
            "feature_names"
        ]
    ):
        raise PredictionError(
            "MODEL_INCOMPATIBLE",
            (
                "Engineered feature order differs "
                "from the bound artifact."
            ),
        )

    if not features.valid_mask.iloc[
        -1
    ]:
        raise ValueError(
            "Latest completed candle lacks valid model features"
        )

    return features


def _validate_prediction_origin(
    history: pd.DataFrame,
    contract: dict[str, Any],
) -> Any:
    origin = history.index[
        -1
    ]

    available_after = (
        pd.to_datetime(
            contract[
                "fit_dates"
            ][
                "validation"
            ][
                "label_end"
            ],
            utc=True,
        )
    )

    origin_utc = (
        pd.to_datetime(
            origin,
            utc=True,
        )
    )

    if (
        origin_utc
        <= available_after
    ):
        raise ValueError(
            "Prediction origin must follow model validation outcomes"
        )

    return origin


def _predict_probability(
    candidate: Any,
    features: Any,
) -> float:
    probability = float(
        candidate.predict_proba(
            features
            .all_features
            .iloc[[-1]]
        )[0]
    )

    if (
        not np.isfinite(
            probability
        )
        or not 0
        <= probability
        <= 1
    ):
        raise ValueError(
            "Invalid model probability"
        )

    return probability


def _run_inference(
    ticker: str,
    candidate: Any,
    contract: dict[str, Any],
    clock: pd.Timestamp,
) -> tuple[
    float,
    Any,
    pd.Timestamp,
]:
    history, local_clock = (
        _completed_history(
            ticker,
            clock,
        )
    )

    history = (
        _restrict_to_artifact_history(
            history,
            contract,
        )
    )

    features = (
        _validate_features(
            history,
            contract,
        )
    )

    origin = (
        _validate_prediction_origin(
            history,
            contract,
        )
    )

    probability = (
        _predict_probability(
            candidate,
            features,
        )
    )

    return (
        probability,
        origin,
        local_clock,
    )


def _prediction_response(
    *,
    ticker: str,
    model: str,
    version: str,
    contract: dict[str, Any],
    config: PredictionConfig,
    clock: pd.Timestamp,
    local_clock: pd.Timestamp,
    origin: Any,
    probability: float,
) -> dict[str, Any]:
    trained_until = (
        contract[
            "fit_dates"
        ][
            "train"
        ][
            "label_end"
        ]
    )

    trained_timestamp = (
        pd.to_datetime(
            trained_until,
            utc=True,
        )
    )

    age_days = max(
        0,
        (
            clock
            - trained_timestamp
        ).days,
    )

    data_age = max(
        0,
        (
            local_clock.date()
            - origin.date()
        ).days,
    )

    action = (
        "LONG"
        if probability
        >= config.decision_threshold
        else "FLAT"
    )

    return {
        "schema_version": (
            "prediction-v2"
        ),
        "ticker": ticker,
        "symbol": ticker,
        "timestamp": (
            origin.isoformat()
        ),
        "generated_at": (
            clock.isoformat()
        ),
        "interval": (
            config.interval
        ),
        "prediction": {
            "task": (
                "binary_return_event"
            ),
            "horizon": (
                config.horizon
            ),
            "event_threshold": (
                config.event_threshold
            ),
            "probability_up": (
                probability
            ),
            "target_definition": (
                "log(Close[t+5] / Close[t]) > 0.002"
            ),
            "calibrated": False,
        },
        "signal": {
            "action": action,
            "decision_threshold": (
                config.decision_threshold
            ),
            "mode": (
                "research"
            ),
        },
        "model": {
            "type": model,
            "version": version,
            "trained_until": (
                trained_until
            ),
            "validated_until": (
                contract[
                    "fit_dates"
                ][
                    "validation"
                ][
                    "label_end"
                ]
            ),
            "age_days": (
                age_days
            ),
            "stale": (
                age_days
                > config.stale_after_days
            ),
        },
        "market": {
            "completed_candles_only": (
                True
            ),
            "age_days": (
                data_age
            ),
            "stale": (
                data_age
                > config.market_stale_after_days
            ),
            "price_convention": (
                "auto_adjusted_ohlc"
            ),
        },
        "limitations": (
            "Uncalibrated research estimate. "
            "Incremental economic value is not established. "
            "LONG/FLAT is not a trade execution."
        ),
    }


def predict(
    ticker: str,
    model: str = "xgboost",
    *,
    manifest: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    ticker = _validate_ticker(
        ticker
    )

    candidate, contract, version = (
        load_bound_model(
            ticker,
            model,
            manifest,
        )
    )

    config = (
        PredictionConfig()
    )

    clock = (
        _prediction_clock(
            now
        )
    )

    try:
        (
            probability,
            origin,
            local_clock,
        ) = _run_inference(
            ticker,
            candidate,
            contract,
            clock,
        )

    except PredictionError:
        raise

    except Exception as exc:
        logger.warning(
            "Prediction data/inference failed for %s: %s",
            ticker,
            exc,
        )

        raise PredictionError(
            "PREDICTION_UNAVAILABLE",
            str(exc),
        ) from exc

    return _prediction_response(
        ticker=ticker,
        model=model,
        version=version,
        contract=contract,
        config=config,
        clock=clock,
        local_clock=local_clock,
        origin=origin,
        probability=probability,
    )
