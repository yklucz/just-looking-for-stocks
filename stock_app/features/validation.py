"""Single-asset OHLCV validation and timestamp normalization, without filling."""

import numpy as np
import pandas as pd

from ..config import FeatureConfig
from ..targets.target_builder import validate_prices
from .numerics import exceeds_price_bound


MARKET_COLUMNS = (
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
)

IGNORED_COLUMNS = {
    "Adj Close",
    "Dividends",
    "Stock Splits",
    "Capital Gains",
}


def _validate_history_input(
    history: pd.DataFrame,
) -> None:
    """Validate the basic DataFrame structure."""

    if not isinstance(
        history,
        pd.DataFrame,
    ):
        raise ValueError(
            "History must be a DataFrame "
            "with unique OHLCV columns"
        )

    if not history.columns.is_unique:
        raise ValueError(
            "History must be a DataFrame "
            "with unique OHLCV columns"
        )


def _normalize_timestamp_source(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Normalize Date/Datetime columns into a DatetimeIndex."""

    date_columns = [
        name
        for name in (
            "Date",
            "Datetime",
        )
        if name in frame
    ]

    if not date_columns:
        return frame

    if (
        len(date_columns) != 1
        or isinstance(
            frame.index,
            pd.DatetimeIndex,
        )
    ):
        raise ValueError(
            "Provide one timestamp source: "
            "DatetimeIndex OR Date/Datetime column"
        )

    timestamp_column = (
        date_columns[0]
    )

    timestamps = pd.to_datetime(
        frame.pop(
            timestamp_column
        ),
        errors="raise",
    )

    frame.index = (
        pd.DatetimeIndex(
            timestamps
        )
    )

    return frame


def _validate_market_columns(
    frame: pd.DataFrame,
    config: FeatureConfig,
) -> None:
    """Validate allowed and required market columns."""

    allowed_columns = (
        set(MARKET_COLUMNS)
        | IGNORED_COLUMNS
    )

    unknown = (
        set(frame.columns)
        - allowed_columns
    )

    if unknown:
        raise ValueError(
            "Only market inputs allowed; "
            f"unexpected columns: "
            f"{sorted(unknown, key=str)}"
        )

    required = (
        set(
            config.required_columns
        )
        | {"Close"}
    )

    missing = (
        required
        - set(frame.columns)
    )

    if missing:
        raise ValueError(
            "Missing required market columns: "
            f"{sorted(missing)}"
        )


def _market_frame(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Return numeric OHLCV columns in canonical order."""

    columns = [
        name
        for name in MARKET_COLUMNS
        if name in frame
    ]

    return frame.loc[
        :,
        columns,
    ].astype(float)


def _contains_invalid_sign(
    name: str,
    values: np.ndarray,
) -> bool:
    """Check price and volume sign requirements."""

    if name == "Volume":
        return bool(
            (values < 0).any()
        )

    return bool(
        (values <= 0).any()
    )


def _raise_invalid_column(
    name: str,
) -> None:
    """Raise a consistent validation error."""

    if name == "Volume":
        requirement = (
            "nonnegative"
        )
    else:
        requirement = (
            "positive"
        )

    raise ValueError(
        f"{name} must be finite "
        f"and {requirement}"
    )


def _validate_column_values(
    frame: pd.DataFrame,
) -> None:
    """Validate finite values and valid signs."""

    validate_prices(
        frame["Close"]
    )

    # DataFrame.columns is typed by pandas as
    # Index[Hashable], even though this function has
    # already restricted the columns to known strings.
    for column in frame.columns:
        name = str(column)

        values = frame[
            name
        ].to_numpy(
            dtype=float,
        )

        if not np.isfinite(
            values
        ).all():
            _raise_invalid_column(
                name
            )

        if _contains_invalid_sign(
            name,
            values,
        ):
            _raise_invalid_column(
                name
            )


def _validate_high_low(
    frame: pd.DataFrame,
) -> None:
    """Ensure Low never exceeds High."""

    if (
        "High" not in frame
        or "Low" not in frame
    ):
        return

    if (
        exceeds_price_bound(frame["Low"], frame["High"])
    ).any():
        raise ValueError(
            "Invalid OHLC: "
            "Low exceeds High"
        )


def _outside_low_high(
    frame: pd.DataFrame,
    name: str,
) -> bool:
    """Return whether a value lies outside its candle range."""

    below_low = False
    above_high = False

    if "Low" in frame:
        below_low = bool(
            (
                exceeds_price_bound(frame["Low"], frame[name])
            ).any()
        )

    if "High" in frame:
        above_high = bool(
            (
                exceeds_price_bound(frame[name], frame["High"])
            ).any()
        )

    return (
        below_low
        or above_high
    )


def _validate_open_close_bounds(
    frame: pd.DataFrame,
) -> None:
    """Ensure Open and Close remain within Low/High."""

    for name in (
        "Open",
        "Close",
    ):
        if name not in frame:
            continue

        if _outside_low_high(
            frame,
            name,
        ):
            raise ValueError(
                "Invalid OHLC: "
                f"{name} outside Low/High"
            )


def _validate_ohlc_relationships(
    frame: pd.DataFrame,
) -> None:
    """Validate relationships between OHLC values."""

    _validate_high_low(
        frame
    )

    _validate_open_close_bounds(
        frame
    )


def validate_history(
    history: pd.DataFrame,
    config: FeatureConfig,
) -> pd.DataFrame:
    """Validate and normalize a single-asset OHLCV history frame."""

    _validate_history_input(
        history
    )

    frame = history.copy()

    frame = (
        _normalize_timestamp_source(
            frame
        )
    )

    _validate_market_columns(
        frame,
        config,
    )

    frame = _market_frame(
        frame
    )

    _validate_column_values(
        frame
    )

    _validate_ohlc_relationships(
        frame
    )

    return frame
