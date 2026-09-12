import numpy as np
import pandas as pd
import pytest

from stock_app.config import TargetConfig
from stock_app.training.preprocessing import prepare_training


@pytest.mark.parametrize("horizon", [1, 5, 10])
@pytest.mark.parametrize("partition", ["train", "validation", "test"])
def test_each_sequence_ends_at_origin_and_label_uses_horizon(prices, horizon, partition):
    prepared = prepare_training(prices, 8, TargetConfig(horizon=horizon))
    x, y = prepared.samples(partition)
    origins = getattr(prepared.split, partition)
    assert x.shape == (len(origins), 8, 1)
    for i, t in enumerate(origins):
        recovered = prepared.scaler.inverse_transform(x[i])[:, 0]
        np.testing.assert_allclose(recovered, prices.iloc[t - 7:t + 1], rtol=1e-6)
        assert y[i] == pytest.approx(np.log(prices.iloc[t + horizon] / prices.iloc[t]))


def test_legacy_price_target_and_prior_partition_context(prices):
    prepared = prepare_training(prices, 8, TargetConfig(task="legacy_price"))
    x, y = prepared.samples("validation")
    t = prepared.split.validation[0]
    assert t - 7 < prepared.split.train_end
    assert prepared.scaler.inverse_transform(y.reshape(-1, 1))[0, 0] == pytest.approx(prices.iloc[t + 1])
    assert prepared.scaler.inverse_transform(x[0])[-1, 0] == pytest.approx(prices.iloc[t])


def test_twenty_candle_horizon_with_sufficient_history():
    prices = pd.Series(np.exp(np.arange(300) / 1000),
                       index=pd.bdate_range("2020-01-01", periods=300))
    prepared = prepare_training(prices, 32, TargetConfig(horizon=20))
    for partition in ("train", "validation", "test"):
        x, y = prepared.samples(partition)
        assert x.shape[1:] == (32, 1)
        np.testing.assert_allclose(y, 0.02)
