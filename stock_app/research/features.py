"""Additional context features only; concatenate with baseline all_features.

Every row is a forecast origin after the asset's exchange close. Context closes
must be from that same session and available at or before that origin. No
forward-fill, zero-fill, backward-fill or future-session matches are used.
"""
import numpy as np
import pandas as pd
from .calendar import session_close, session_index


def _availability(frame):
    if 'AvailableAt' in frame:
        return pd.Series(pd.to_datetime(frame['AvailableAt'], utc=True).to_numpy(), index=frame.index)
    if 'available_at' in frame:
        return pd.Series(pd.to_datetime(frame['available_at'], utc=True).to_numpy(), index=frame.index)
    values = []
    for day in frame.index:
        try:
            values.append(session_close(day, frame.attrs.get('exchange', 'XNYS')))
        except ValueError:
            values.append(pd.NaT)
    return pd.Series(pd.to_datetime(values, utc=True), index=frame.index)


def build_context_features(history, contexts, *, market='SPY', sector='XLK'):
    if not isinstance(history.index, pd.DatetimeIndex) or not history.index.is_unique:
        raise ValueError('Unique timestamp-indexed history required')
    original_index = history.index
    asset = history.copy()
    asset.index = session_index(asset.index)
    if not asset.index.is_unique or not asset.index.is_monotonic_increasing:
        raise ValueError('Sorted unique sessions required')
    origin = _availability(asset)
    close = asset.Close.astype(float)
    asset_returns = {h: close.pct_change(h, fill_method=None) for h in (1,5,20)}
    output = pd.DataFrame(index=asset.index)
    for prefix, symbol in (('market', market), ('sector', sector)):
        context = contexts.get(symbol) if symbol else None
        context_close = pd.Series(np.nan, index=asset.index, dtype=float)
        if context is not None and not context.empty:
            context = context.copy()
            context.index = session_index(context.index)
            if not context.index.is_unique or not context.index.is_monotonic_increasing:
                raise ValueError('Context sessions must be sorted and unique')
            availability = _availability(context).reindex(asset.index)
            visible = availability.notna() & origin.notna() & (availability <= origin)
            context_close = context.Close.astype(float).reindex(asset.index).where(visible)
        returns = {}
        for horizon in (1,5,20):
            returns[horizon] = context_close.pct_change(horizon, fill_method=None)
            # An absent session anywhere in a window cannot silently become a
            # multi-session return represented as a complete session window.
            complete = context_close.notna().rolling(horizon+1, min_periods=horizon+1).sum() == horizon+1
            returns[horizon] = returns[horizon].where(complete)
            output[f'{prefix}_return_{horizon}'] = returns[horizon]
            output[f'{prefix}_relative_return_{horizon}'] = asset_returns[horizon] - returns[horizon]
        variance = returns[1].rolling(60, min_periods=60).var()
        output[f'{prefix}_beta_60'] = asset_returns[1].rolling(60, min_periods=60).cov(returns[1]) / variance.replace(0, np.nan)
        output[f'{prefix}_correlation_60'] = asset_returns[1].rolling(60, min_periods=60).corr(returns[1])
        if prefix == 'market':
            output['market_volatility_20'] = returns[1].rolling(20, min_periods=20).std() * np.sqrt(252)
    output['overnight_gap'] = asset.Open / close.shift(1) - 1
    output['drawdown_20'] = close / close.rolling(20, min_periods=20).max() - 1
    true_range = pd.concat([asset.High-asset.Low, (asset.High-close.shift(1)).abs(),
                            (asset.Low-close.shift(1)).abs()], axis=1).max(axis=1)
    output['atr_to_close_14'] = true_range.rolling(14, min_periods=14).mean() / close
    output = output.replace([np.inf, -np.inf], np.nan)
    output.index = original_index
    output.attrs['feature_set'] = 'context-v1'
    output.attrs['availability_rule'] = 'same session and available_at <= asset close; no filling'
    return output
