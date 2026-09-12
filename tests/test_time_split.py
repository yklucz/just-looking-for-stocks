import numpy as np
import pytest

from stock_app.config import SplitConfig
from stock_app.training.time_split import chronological_split


@pytest.mark.parametrize("horizon", [1, 5, 10])
def test_disjoint_origins_and_purged_labels(prices, horizon):
    split = chronological_split(prices.index, lookback=10, horizon=horizon)
    assert split.train_end == 84
    assert split.validation_end == 102
    assert split.train.max() + horizon < split.validation.min()
    assert split.validation.max() + horizon < split.test.min()
    assert split.test.max() + horizon == len(prices) - 1
    for a, b in [(split.train, split.validation), (split.validation, split.test), (split.train, split.test)]:
        assert not np.intersect1d(a, b).size


def test_short_history_refuses_to_fall_back_to_training_only(prices):
    with pytest.raises(ValueError, match="Not enough"):
        chronological_split(prices.index[:22], lookback=20, horizon=1)


@pytest.mark.parametrize("fractions", [(0, .1), (.9, .1), (.5, 0), (float("nan"), .1)])
def test_invalid_split_config(fractions):
    with pytest.raises(ValueError):
        SplitConfig(*fractions)
