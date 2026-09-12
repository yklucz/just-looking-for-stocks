import numpy as np
import pandas as pd
import pytest
from sklearn.exceptions import NotFittedError
from sklearn.preprocessing import MinMaxScaler

from stock_app.config import TargetConfig
from stock_app.training.preprocessing import inference_window, prepare_training, transform_inference


def test_future_outliers_do_not_change_training_scaler_or_samples(prices):
    original = prepare_training(prices, 10, TargetConfig(horizon=5))
    changed = prices.copy()
    changed.iloc[original.split.train_end:] *= 1000
    future = prepare_training(changed, 10, TargetConfig(horizon=5))
    assert original.scaler.n_samples_seen_ == original.split.train_end
    np.testing.assert_array_equal(original.scaler.data_max_, [prices.iloc[83]])
    np.testing.assert_array_equal(original.scaler.data_max_, future.scaler.data_max_)
    for a, b in zip(original.samples("train"), future.samples("train")):
        np.testing.assert_array_equal(a, b)
    assert future.scaled[-1, 0] > 1


def test_test_outliers_do_not_change_validation(prices):
    prepared = prepare_training(prices, 10, TargetConfig(horizon=5))
    changed = prices.copy()
    changed.iloc[prepared.split.validation_end:] *= 100
    altered = prepare_training(changed, 10, TargetConfig(horizon=5))
    for a, b in zip(prepared.samples("validation"), altered.samples("validation")):
        np.testing.assert_array_equal(a, b)


def test_inference_never_refits_and_requires_fitted_scaler(prices, monkeypatch):
    with pytest.raises(NotFittedError):
        transform_inference(prices, MinMaxScaler())
    prepared = prepare_training(prices, 10)
    def forbidden(*args, **kwargs):
        raise AssertionError("Inference attempted to fit")
    monkeypatch.setattr(MinMaxScaler, "fit", forbidden)
    monkeypatch.setattr(MinMaxScaler, "fit_transform", forbidden)
    inference = inference_window(prices, prepared.scaler, 10)
    np.testing.assert_array_equal(inference[0], prepared.scaled[-10:])
    reused = prepare_training(prices, 10, scaler=prepared.scaler)
    np.testing.assert_array_equal(reused.scaled, prepared.scaled)


def test_full_dataset_scaler_cannot_be_reused_for_evaluation(prices):
    leaked_scaler = MinMaxScaler().fit(prices.to_numpy().reshape(-1, 1))
    with pytest.raises(ValueError, match="TRAIN"):
        prepare_training(prices, 10, scaler=leaked_scaler)


@pytest.mark.parametrize("problem", ["duplicate", "reverse", "nat", "nan", "inf", "zero", "negative"])
def test_bad_input_fails_closed(prices, problem):
    bad = prices.copy()
    if problem == "duplicate":
        bad.index = pd.DatetimeIndex([bad.index[0]] + list(bad.index[:-1]))
    elif problem == "reverse":
        bad = bad.iloc[::-1]
    elif problem == "nat":
        bad.index = pd.DatetimeIndex([pd.NaT] + list(bad.index[1:]))
    else:
        bad.iloc[3] = {"nan": np.nan, "inf": np.inf, "zero": 0, "negative": -1}[problem]
    with pytest.raises(ValueError):
        prepare_training(bad, 10)
