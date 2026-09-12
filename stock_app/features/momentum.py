"""Wilder RSI needs n+1 candles; MACD signal needs slow+signal-1."""
import pandas as pd
from ..config import FeatureConfig
from .common import FeatureColumns, divide, ema, wilder


def build_momentum(close: pd.Series, config: FeatureConfig) -> FeatureColumns:
    delta = close.diff().iloc[1:]
    gains = wilder(delta.clip(lower=0), config.rsi_window).reindex(close.index)
    losses = wilder(-delta.clip(upper=0), config.rsi_window).reindex(close.index)
    rsi = 100 * divide(gains, gains + losses)
    # A fully flat seeded window has neutral RSI; this is an explicit convention.
    rsi = rsi.mask((gains == 0) & (losses == 0), 50.0)
    columns = {f"rsi_{config.rsi_window}": (rsi, config.rsi_window + 1)}
    columns.update({f"roc_{n}": (100 * (divide(close, close.shift(n)) - 1), n + 1)
                    for n in config.roc_windows})
    macd = ema(close, config.macd_fast) - ema(close, config.macd_slow)
    signal = ema(macd, config.macd_signal)
    signal_lookback = config.macd_slow + config.macd_signal - 1
    columns.update(macd=(macd, config.macd_slow), macd_signal=(signal, signal_lookback),
                   macd_histogram=(macd - signal, signal_lookback))
    return columns
