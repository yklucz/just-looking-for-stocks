from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from stock_app.config import FeatureConfig
from stock_app.features import build_features


@pytest.fixture
def candles():
    rng = np.random.default_rng(73)
    close = 100 * np.exp(np.cumsum(rng.normal(.0002, .01, 1000)))
    opening = close * np.exp(rng.normal(0, .002, len(close)))
    return pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) * 1.01,
                         "Low": np.minimum(opening, close) * .99, "Close": close,
                         "Volume": rng.integers(1000, 10000, len(close)).astype(float)},
                        index=pd.bdate_range("2020-01-01", periods=len(close)))


def recursive_average(values, alpha):
    result = np.empty(len(values))
    result[0] = values[0]
    for i in range(1, len(values)):
        result[i] = alpha * values[i] + (1 - alpha) * result[i - 1]
    return result


def test_returns_are_causal(candles):
    features = build_features(candles).all_features
    for n in (1, 2, 5, 10, 20):
        assert features[f"return_{n}"].iloc[60] == pytest.approx(candles.Close.iloc[60] / candles.Close.iloc[60-n] - 1)
    for n in (1, 5, 20):
        assert features[f"log_return_{n}"].iloc[60] == pytest.approx(np.log(candles.Close.iloc[60] / candles.Close.iloc[60-n]))


def test_sma_is_causal(candles):
    result = build_features(candles).all_features
    for n in (5, 10, 20, 50, 200):
        assert result[f"sma_{n}"].iloc[250] == pytest.approx(candles.Close.iloc[251-n:251].mean())
    assert result.close_sma_20_ratio.iloc[250] == pytest.approx(candles.Close.iloc[250] / result.sma_20.iloc[250] - 1)


def test_ema_is_causal(candles):
    result = build_features(candles).all_features
    for n in (10, 20, 50):
        expected = recursive_average(candles.Close.to_numpy(), 2 / (n + 1))
        np.testing.assert_allclose(result[f"ema_{n}"].iloc[n-1:], expected[n-1:])
        np.testing.assert_allclose(result[f"close_ema_{n}_ratio"].iloc[n-1:], candles.Close.iloc[n-1:] / expected[n-1:] - 1)


def test_rsi_is_causal(candles):
    delta = np.diff(candles.Close)
    gains, losses = np.maximum(delta, 0), np.maximum(-delta, 0)
    g = recursive_average(np.r_[gains[:14].mean(), gains[14:]], 1 / 14)
    loss = recursive_average(np.r_[losses[:14].mean(), losses[14:]], 1 / 14)
    actual = build_features(candles).all_features.rsi_14
    assert actual.iloc[:14].isna().all()
    np.testing.assert_allclose(actual.iloc[14:], 100 * g / (g + loss))


def test_macd_is_causal(candles):
    values = candles.Close.to_numpy()
    expected = recursive_average(values, 2/13) - recursive_average(values, 2/27)
    signal = recursive_average(expected[25:], 2/10)
    result = build_features(candles).all_features
    np.testing.assert_allclose(result.macd.iloc[25:], expected[25:])
    np.testing.assert_allclose(result.macd_signal.iloc[33:], signal[8:])
    np.testing.assert_allclose(result.macd_histogram.iloc[33:], expected[33:] - signal[8:])
    np.testing.assert_allclose(result.roc_5.iloc[5:], result.return_5.iloc[5:] * 100)


def test_volatility_is_causal(candles):
    result = build_features(candles).all_features
    log_returns = np.diff(np.log(candles.Close))
    for n in (5, 20, 60):
        assert result[f"volatility_{n}"].iloc[250] == pytest.approx(np.std(log_returns[250-n:250], ddof=1))


def test_atr_is_causal(candles):
    high, low, close = candles.High.to_numpy(), candles.Low.to_numpy(), candles.Close.to_numpy()
    tr = np.maximum.reduce([high[1:] - low[1:], abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])])
    atr = recursive_average(np.r_[tr[:14].mean(), tr[14:]], 1/14)
    actual = build_features(candles).all_features
    assert np.isnan(actual.true_range.iloc[0])
    np.testing.assert_allclose(actual.true_range.iloc[1:], tr)
    np.testing.assert_allclose(actual.atr_14.iloc[14:], atr)


def test_volume_features_are_causal(candles):
    result = build_features(candles).all_features
    v = candles.Volume
    assert result.volume_change.iloc[40] == pytest.approx(v.iloc[40] / v.iloc[39] - 1)
    for n in (5, 20):
        mean = v.iloc[41-n:41].mean()
        assert result[f"volume_mean_{n}"].iloc[40] == pytest.approx(mean)
        assert result[f"relative_volume_{n}"].iloc[40] == pytest.approx(v.iloc[40] / mean)
    expected = (v.iloc[40] - v.iloc[21:41].mean()) / v.iloc[21:41].std(ddof=1)
    assert result.volume_zscore_20.iloc[40] == pytest.approx(expected)


def test_candle_normalization(candles):
    actual = build_features(candles).all_features.iloc[35]
    row = candles.iloc[35]
    assert actual.high_low_range == pytest.approx((row.High - row.Low) / row.Close)
    assert actual.close_open_return == pytest.approx(row.Close / row.Open - 1)
    assert actual.body_size == pytest.approx(abs(row.Close - row.Open) / row.Close)
    assert actual.upper_wick == pytest.approx((row.High - max(row.Open, row.Close)) / row.Close)
    assert actual.lower_wick == pytest.approx((min(row.Open, row.Close) - row.Low) / row.Close)


@pytest.mark.parametrize("prefix,total", [(100, 150), (250, 500), (500, 1000)])
def test_prefix_invariance(candles, prefix, total):
    a, b = build_features(candles.iloc[:prefix]), build_features(candles.iloc[:total])
    # Compare every diagnostic value, even before all SMA200 model rows are ready.
    assert_frame_equal(a.all_features, b.all_features.iloc[:prefix], rtol=1e-12, atol=1e-12)
    assert_frame_equal(a.frame, b.frame.loc[b.frame.index < candles.index[prefix]], rtol=1e-12, atol=1e-12)


def test_short_prefix_with_nonempty_model_rows(candles):
    config = replace(FeatureConfig(), sma_windows=(5, 10, 20, 50))
    a, b = build_features(candles.iloc[:100], config), build_features(candles.iloc[:150], config)
    assert len(a.frame) == 40
    assert_frame_equal(a.frame, b.frame.loc[a.frame.index])


@pytest.mark.parametrize("columns,factor", [(["Open", "High", "Low", "Close"], 100), (["Volume"], 1000)])
def test_future_outlier_invariance(candles, columns, factor):
    changed = candles.copy()
    changed.loc[changed.index[500:], columns] *= factor
    a, b = build_features(candles), build_features(changed)
    assert_frame_equal(a.all_features.iloc[:500], b.all_features.iloc[:500], rtol=1e-12, atol=1e-12)
    assert not a.all_features.iloc[550:].equals(b.all_features.iloc[550:])


def test_required_lookback_and_warmup(candles):
    result = build_features(candles)
    assert result.max_lookback == 200
    assert result.warmup_rows == 199
    assert len(result.frame) == 801
    assert result.metadata["invalid_rows_after_warmup"] == 0
    for name, count in result.lookbacks.items():
        assert result.all_features[name].first_valid_index() == candles.index[count - 1]
    assert {"ema_50", "macd_signal", "rsi_14", "atr_14"} <= set(result.recursive_features)


def test_stable_feature_order(candles):
    before = candles.copy(deep=True)
    a = build_features(candles)
    b = build_features(candles[list(reversed(candles.columns))])
    assert len(a.feature_names) == 49
    assert a.feature_names == b.feature_names
    assert a.feature_names[:5] == ("return_1", "return_2", "return_5", "return_10", "return_20")
    assert_frame_equal(a.frame, b.frame)
    assert_frame_equal(candles, before)


def test_missing_volume_handling(candles):
    result = build_features(candles.drop(columns="Volume"))
    assert len(result.feature_names) == 43
    assert result.skipped_groups == ("volume",)
    with pytest.raises(ValueError, match="Volume"):
        build_features(candles.drop(columns="Volume"), replace(FeatureConfig(), required_columns=("Volume",)))


def test_missing_ohlc_handling(candles):
    result = build_features(candles[["Close"]])
    assert len(result.feature_names) == 36
    assert result.skipped_groups == ("true_range_atr", "candle", "volume")
    assert "atr_14" not in result.feature_names
    with pytest.raises(ValueError, match="High"):
        build_features(candles[["Close"]], replace(FeatureConfig(), required_columns=("High",)))
    with pytest.raises(ValueError, match="Close"):
        build_features(candles.drop(columns="Close"))


@pytest.mark.parametrize("problem", ["duplicate", "reverse", "nat", "nan", "infinity", "zero_price", "negative_volume", "bad_ohlc"])
def test_invalid_market_input(candles, problem):
    bad = candles.copy()
    if problem == "duplicate":
        bad.index = pd.DatetimeIndex([bad.index[0]] + list(bad.index[:-1]))
    elif problem == "reverse":
        bad = bad.iloc[::-1]
    elif problem == "nat":
        bad.index = pd.DatetimeIndex([pd.NaT] + list(bad.index[1:]))
    else:
        column, value = {"nan": ("Close", np.nan), "infinity": ("High", np.inf),
                         "zero_price": ("Close", 0), "negative_volume": ("Volume", -1),
                         "bad_ohlc": ("Low", 1e6)}[problem]
        bad.iloc[20, bad.columns.get_loc(column)] = value
    with pytest.raises(ValueError):
        build_features(bad)


def test_no_infinite_features_and_zero_volume(candles):
    candles.loc[candles.index[250:280], "Volume"] = 0
    result = build_features(candles)
    assert not np.isinf(result.all_features.to_numpy()).any()
    assert np.isfinite(result.frame.to_numpy()).all()
    assert np.isnan(result.all_features.volume_change.iloc[280])
    assert np.isnan(result.all_features.volume_zscore_20.iloc[275])
    assert not result.valid_mask.iloc[280]
    assert result.valid_mask.iloc[-1]


def test_constant_windows_are_explicit(candles):
    candles.loc[:, ["Open", "High", "Low", "Close"]] = 100
    candles.Volume = 0
    result = build_features(candles)
    assert (result.all_features.rsi_14.iloc[14:] == 50).all()
    assert (result.all_features.volatility_20.iloc[20:] == 0).all()
    assert result.all_features.relative_volume_20.isna().all()
    assert result.all_features.volume_zscore_20.isna().all()
    assert result.frame.empty


def test_target_isolation(candles):
    for name in ("target", "future_log_return", "target_time", "y"):
        with pytest.raises(ValueError, match="Only market inputs"):
            build_features(candles.assign(**{name: 999}))


def test_time_features_and_timestamp_adapter(candles):
    daily = build_features(candles)
    assert "hour_sin" not in daily.feature_names
    dated = candles.reset_index(names="Date")
    dated.Date = dated.Date.dt.strftime("%Y-%m-%d")
    assert_frame_equal(build_features(dated).frame, daily.frame, check_names=False, check_freq=False)
    candles.index = pd.date_range("2020-01-01 09:30", periods=len(candles), freq="min", tz="America/New_York")
    result = build_features(candles, replace(FeatureConfig(), interval="1m"))
    assert len(result.feature_names) == 53
    assert result.frame.index.tz == candles.index.tz
    first = result.all_features.iloc[0]
    assert first.hour_sin == pytest.approx(np.sin(2*np.pi*9/24))
    assert first.minute_cos == pytest.approx(-1)
    for name in ("day_of_week", "month", "hour", "minute"):
        np.testing.assert_allclose(result.all_features[f"{name}_sin"]**2 + result.all_features[f"{name}_cos"]**2, 1)


def test_group_configuration_and_raw_columns(candles):
    config = FeatureConfig(returns=False, trend=False, momentum=False, volatility=False,
                           candle=False, volume=False, time=False, raw=True)
    result = build_features(candles, config)
    assert result.feature_names == ("open", "high", "low", "close", "volume")
    assert result.max_lookback == 1
    assert len(result.frame) == len(candles)
    with pytest.raises(ValueError, match="no features"):
        build_features(candles, replace(config, raw=False))


@pytest.mark.parametrize("kwargs", [{"return_windows": (0,)}, {"sma_windows": (5, 5)},
                                    {"rsi_window": 0}, {"volatility_windows": (1,)},
                                    {"macd_fast": 26}, {"interval": "auto"},
                                    {"required_columns": ("target",)}])
def test_invalid_feature_config(kwargs):
    with pytest.raises(ValueError):
        FeatureConfig(**kwargs)
