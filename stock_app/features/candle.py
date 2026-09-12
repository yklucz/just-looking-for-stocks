"""Same-candle shape, available only after the candle is complete; lookback 1."""
import pandas as pd
from .common import FeatureColumns, divide


def build_candle(history: pd.DataFrame) -> FeatureColumns:
    top = history[["Open", "Close"]].max(axis=1)
    bottom = history[["Open", "Close"]].min(axis=1)
    return {
        "high_low_range": (divide(history.High - history.Low, history.Close), 1),
        "close_open_return": (divide(history.Close, history.Open) - 1, 1),
        "body_size": (divide((history.Close - history.Open).abs(), history.Close), 1),
        "upper_wick": (divide(history.High - top, history.Close), 1),
        "lower_wick": (divide(bottom - history.Low, history.Close), 1),
    }
