import numpy as np
import pandas as pd
import pytest

from stock_app.config import TargetConfig
from stock_app.targets.target_builder import build_targets


@pytest.mark.parametrize("horizon", [1, 5, 20])
def test_future_return_alignment_and_unknown_tail(prices, horizon):
    result = build_targets(prices, TargetConfig(horizon=horizon))
    assert len(result) == len(prices) - horizon
    np.testing.assert_allclose(result.target, np.log(prices.to_numpy()[horizon:] / prices.to_numpy()[:-horizon]))
    assert result.index.equals(prices.index[:-horizon])
    assert pd.DatetimeIndex(result.target_time).equals(prices.index[horizon:])


def test_class_labels_and_threshold_boundaries():
    prices = pd.Series([1, 2, 2, 1, 4], index=pd.date_range("2020-01-01", periods=5))
    boundary = np.log(2)
    assert build_targets(prices, TargetConfig(task="binary", threshold=boundary)).target.tolist() == [0, 0, 0, 1]
    assert build_targets(prices, TargetConfig(task="three_class", threshold=boundary)).target.tolist() == [1, 1, 1, 2]
    assert build_targets(prices, TargetConfig(task="three_class", threshold=0.1)).target.tolist() == [2, 1, 0, 2]


@pytest.mark.parametrize("task", ["regression", "binary", "three_class", "legacy_price"])
def test_insufficient_target_history_is_empty(prices, task):
    assert build_targets(prices.iloc[:3], TargetConfig(horizon=5, task=task)).empty


@pytest.mark.parametrize("kwargs", [{"horizon": 0}, {"horizon": 1.5}, {"horizon": True},
                                    {"threshold": -1}, {"threshold": float("nan")},
                                    {"threshold": float("inf")}, {"task": "unknown"}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        TargetConfig(**kwargs)
