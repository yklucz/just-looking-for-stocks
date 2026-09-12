import numpy as np
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal
from sklearn.preprocessing import MinMaxScaler

from stock_app.config import TargetConfig
from stock_app.training.feature_dataset import build_feature_dataset
from stock_app.training.feature_preprocessing import fit_feature_preprocessor
from test_features import candles


@pytest.mark.parametrize("horizon", [1, 5, 20])
@pytest.mark.parametrize("task", ["regression", "binary", "three_class"])
def test_feature_target_alignment_and_trailing_unknown_targets_removed(candles, horizon, task):
    data = build_feature_dataset(candles, target_config=TargetConfig(horizon=horizon, task=task))
    assert len(data.X) == len(candles) - 199 - horizon
    assert data.X.index[-1] == candles.index[-horizon - 1]
    assert data.features.frame.index[-1] == candles.index[-1]
    t = 300
    expected = np.log(candles.Close.iloc[t+horizon] / candles.Close.iloc[t])
    label = expected if task == "regression" else int(expected > .002) if task == "binary" else (0 if expected < -.002 else 2 if expected > .002 else 1)
    assert data.y.loc[candles.index[t]] == pytest.approx(label)
    assert data.target_times.loc[candles.index[t]] == candles.index[t+horizon]
    assert data.X.loc[candles.index[t], "return_5"] == pytest.approx(candles.Close.iloc[t] / candles.Close.iloc[t-5] - 1)
    assert data.split.train_end == 700
    assert data.split.validation_end == 850
    assert data.split.train[-1] + horizon < data.split.validation[0]
    assert data.split.validation[-1] + horizon < data.split.test[0]


def test_invalid_interior_rows_do_not_change_horizon_or_sequence_adjacency(candles):
    candles.loc[candles.index[300:330], "Volume"] = 0
    data = build_feature_dataset(candles, target_config=TargetConfig(horizon=5), sequence_length=8)
    assert data.target_times.loc[candles.index[295]] == candles.index[300]
    for name in ("train", "validation", "test"):
        x, y = data.sequences(name)
        assert x.shape[1:] == (8, 49)
        assert len(y) == len(x)
        assert np.isfinite(x).all()
        for row, t in enumerate(getattr(data.split, name)):
            np.testing.assert_array_equal(x[row], data.features.all_features.iloc[t-7:t+1])
    # The first zero volume is valid (-100%); the next divides by zero.
    assert 300 in data.split.train
    assert not any(301 <= t <= 337 for t in data.split.train)


def test_features_keep_pre_split_history(candles):
    data = build_feature_dataset(candles)
    x_val, _ = data.partition("validation")
    t = data.split.validation[0]
    assert x_val.sma_200.iloc[0] == pytest.approx(candles.Close.iloc[t-199:t+1].mean())
    assert data.features.lookbacks["sma_200"] == 200


def test_feature_scaler_train_only_and_transform_only(candles, monkeypatch):
    data = build_feature_dataset(candles, target_config=TargetConfig(horizon=5))
    prep = fit_feature_preprocessor(data)
    train, _ = data.partition("train")
    assert prep.scaler.n_samples_seen_ == len(train)
    np.testing.assert_array_equal(prep.scaler.data_max_, train.max(axis=0))
    changed = candles.copy()
    changed.loc[changed.index[700:], ["Open", "High", "Low", "Close"]] *= 100
    changed.loc[changed.index[700:], "Volume"] *= 1000
    altered = build_feature_dataset(changed, target_config=TargetConfig(horizon=5))
    other = fit_feature_preprocessor(altered)
    assert_frame_equal(train, altered.partition("train")[0])
    assert_series_equal(data.partition("train")[1], altered.partition("train")[1])
    np.testing.assert_array_equal(prep.scaler.data_max_, other.scaler.data_max_)
    def forbidden(*args, **kwargs):
        raise AssertionError("Transform attempted to fit")
    monkeypatch.setattr(MinMaxScaler, "fit", forbidden)
    monkeypatch.setattr(MinMaxScaler, "fit_transform", forbidden)
    assert (prep.transform(altered.partition("test")[0]).to_numpy() > 1).any()
    assert prep.transform(data.features.frame.tail(1)).shape == (1, 49)
    with pytest.raises(ValueError, match="names/order"):
        prep.transform(train[list(reversed(train.columns))])
    with pytest.raises(ValueError, match="finite"):
        prep.transform(data.features.all_features)


def test_test_outliers_do_not_change_validation_or_targets(candles):
    before = build_feature_dataset(candles, target_config=TargetConfig(horizon=20))
    candles.loc[candles.index[850:], ["Open", "High", "Low", "Close"]] *= 100
    candles.loc[candles.index[850:], "Volume"] *= 1000
    after = build_feature_dataset(candles, target_config=TargetConfig(horizon=20))
    for name in ("train", "validation"):
        assert_frame_equal(before.partition(name)[0], after.partition(name)[0])
        assert_series_equal(before.partition(name)[1], after.partition(name)[1])


def test_insufficient_feature_history_fails_clearly(candles):
    with pytest.raises(ValueError, match="empty"):
        build_feature_dataset(candles.iloc[:250])
