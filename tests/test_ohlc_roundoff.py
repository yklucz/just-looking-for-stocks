"""Provider adjustment roundoff must not bypass genuine market-data checks."""
import numpy as np
import pandas as pd
import pytest

from stock_app.config import FeatureConfig
from stock_app.features.validation import validate_history
from stock_app.prediction.service import _trim_invalid_trailing_rows


def frame(price):
    return pd.DataFrame({'Open': [price]*3, 'High': [price]*3, 'Low': [price]*3,
                         'Close': [price]*3, 'Volume': [100]*3},
                        index=pd.date_range('2020-01-01', periods=3))


@pytest.mark.parametrize('price', [.01, 100., 100000.])
@pytest.mark.parametrize('field,direction', [('Close', np.inf), ('Close', -np.inf),
                                           ('Open', np.inf), ('Open', -np.inf)])
def test_roundoff_retains_candles_without_changing_prices(price, field, direction):
    data = frame(price)
    data.loc[data.index[1], field] = np.nextafter(price, direction)
    original = data.copy()
    result = _trim_invalid_trailing_rows(data)
    validated = validate_history(result, FeatureConfig())
    pd.testing.assert_frame_equal(result, original)
    np.testing.assert_array_equal(validated.to_numpy(), original.to_numpy())


@pytest.mark.parametrize('value', [100.00000001, 99.99999999, np.nan, np.inf, 0., -1.])
def test_real_bad_historical_prices_still_fail_with_date(value):
    data = frame(100.)
    data.loc[data.index[1], 'Close'] = value
    with pytest.raises(ValueError, match='historical context at 2020-01-02'):
        _trim_invalid_trailing_rows(data)
    with pytest.raises(ValueError):
        validate_history(data, FeatureConfig())


def test_small_high_low_roundoff_is_consistent():
    data = frame(100.)
    data.loc[data.index[1], 'Low'] = np.nextafter(100., np.inf)
    assert len(validate_history(_trim_invalid_trailing_rows(data), FeatureConfig())) == 3


def test_negative_volume_remains_invalid():
    data = frame(100.)
    data.loc[data.index[1], 'Volume'] = -1
    with pytest.raises(ValueError, match='historical context at 2020-01-02'):
        _trim_invalid_trailing_rows(data)
