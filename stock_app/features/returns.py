"""Trailing returns need n+1 candles, including the current candle."""

import numpy as np
import pandas as pd

from ..config import FeatureConfig
from .common import FeatureColumns, divide


def build_returns(
    close: pd.Series,
    config: FeatureConfig,
) -> FeatureColumns:
    columns: FeatureColumns = {}

    # Normal percentage returns.
    for window in config.return_windows:
        columns[f"return_{window}"] = (
            divide(
                close,
                close.shift(window),
            )
            - 1,
            window + 1,
        )

    # Explicitly keep the log result as a pandas Series.
    # Pylance otherwise interprets np.log(pd.Series)
    # as a NumPy ndarray.
    log_close = pd.Series(
        np.log(
            close.to_numpy(
                dtype=float,
            )
        ),
        index=close.index,
        name=close.name,
        dtype=float,
    )

    for window in config.log_return_windows:
        columns[f"log_return_{window}"] = (
            log_close
            - log_close.shift(window),
            window + 1,
        )

    return columns