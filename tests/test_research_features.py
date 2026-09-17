import numpy as np
import pandas as pd
import pytest
from stock_app.research.features import build_context_features
from stock_app.research.events import validate_event_records, training_event_records


def market_frame(n=90):
    index = pd.bdate_range('2024-01-02', periods=n, tz='UTC')
    close = pd.Series(100 + np.arange(n) + np.sin(np.arange(n)), index=index)
    return pd.DataFrame({'Open':close-.2,'High':close+1,'Low':close-1,'Close':close,'Volume':100,
                         'AvailableAt': index + pd.Timedelta(hours=21)})


def test_context_is_causal_and_preserves_missing():
    frame = market_frame()
    result = build_context_features(frame, {'SPY':frame,'XLK':frame})
    truncated = build_context_features(frame.iloc[:75], {'SPY':frame,'XLK':frame})
    pd.testing.assert_frame_equal(result.iloc[:75], truncated)
    assert result.index.equals(frame.index)
    assert result['market_beta_60'].iloc[-1] == pytest.approx(1)
    assert result['market_relative_return_20'].iloc[-1] == pytest.approx(0)
    missing = build_context_features(frame,{})
    assert missing['market_return_1'].isna().all()
    assert missing['sector_return_20'].isna().all()
    assert missing['overnight_gap'].notna().sum() == len(frame)-1


def test_late_context_and_missing_sessions_never_fill():
    frame = market_frame()
    context = frame.copy()
    context.loc[context.index[70], 'AvailableAt'] += pd.Timedelta(days=1)
    out = build_context_features(frame, {'SPY':context})
    assert pd.isna(out['market_return_1'].iloc[70])
    context = frame.drop(index=frame.index[70])
    out = build_context_features(frame, {'SPY':context})
    assert pd.isna(out['market_return_1'].iloc[70])
    assert pd.isna(out['market_return_1'].iloc[71])


def test_event_validation_and_training_availability():
    records = [dict(symbol='AAPL',event_date='2024-01-03',available_at='2024-01-02T12:00Z',verified=True,source='filing'),
               dict(symbol='AAPL',event_date='2024-01-04',available_at='2024-01-02T12:00Z',verified=False,source='manual')]
    frame = validate_event_records(records)
    assert len(training_event_records(frame, as_of='2024-01-02T13:00Z')) == 1
    assert len(training_event_records(frame, as_of='2024-01-02T11:00Z')) == 0
    with pytest.raises(ValueError, match='available_at'):
        validate_event_records([dict(symbol='AAPL',event_date='2024-01-03')])
    with pytest.raises(ValueError, match='report_date'):
        validate_event_records(records,kind='fundamentals')
    with pytest.raises(ValueError, match='verified'):
        validate_event_records([dict(records[0], verified='false')])
