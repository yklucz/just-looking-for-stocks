"""Unannualized sample log-return volatility; Wilder ATR uses previous Close."""

import numpy as np
import pandas as pd

from ..config import FeatureConfig
from .common import FeatureColumns, wilder


def build_volatility(
    history: pd.DataFrame,
    config: FeatureConfig,
) -> FeatureColumns:
    close = pd.to_numeric(
        history["Close"],
        errors="coerce",
    )

    # np.log(Series) is typed by Pylance as ndarray in some pandas/numpy
    # stub versions. Reconstructing a Series keeps the pandas type explicit
    # so .diff() and .rolling() are correctly recognized.
    log_close = pd.Series(
        np.log(
            close.to_numpy(
                dtype=float,
            )
        ),
        index=history.index,
        name="log_close",
    )

    returns = log_close.diff()

    columns: FeatureColumns = {
        f"volatility_{window}": (
            returns.rolling(
                window,
                min_periods=window,
            ).std(ddof=1),
            window + 1,
        )
        for window in config.volatility_windows
    }

    if {"High", "Low"} <= set(history.columns):
        high = pd.to_numeric(
            history["High"],
            errors="coerce",
        )

        low = pd.to_numeric(
            history["Low"],
            errors="coerce",
        )

        previous_close = close.shift(1)

        true_range = pd.concat(
            [
                high - low,
                (high - previous_close).abs(),
                (low - previous_close).abs(),
            ],
            axis=1,
        ).max(
            axis=1,
            skipna=False,
        )

        columns["true_range"] = (
            true_range,
            2,
        )

        columns[
            f"atr_{config.atr_window}"
        ] = (
            wilder(
                true_range.iloc[1:],
                config.atr_window,
            ).reindex(
                history.index
            ),
            config.atr_window + 1,
        )

    return columns