import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from stock_app.research.data import DataRepository
from stock_app.research.calendar import session_close


def history():
    index = pd.DatetimeIndex(['2024-11-27', '2024-11-29', '2024-12-02'])
    return pd.DataFrame({'Open': [100., 101., 102.], 'High': [103.,104.,105.],
                         'Low': [99.,100.,101.], 'Close': [102.,103.,104.],
                         'Adj Close': [51.,51.5,52.], 'Volume': [100,200,300],
                         'Dividends': [0.,0.,0.], 'Stock Splits': [0.,0.,0.]}, index=index)


def test_snapshot_adjustment_and_checksum(tmp_path):
    repo = DataRepository(tmp_path)
    meta = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    assert meta['status'] == 'valid'
    loaded = repo.load(meta['id'])
    assert list(loaded['Close']) == [51., 51.5, 52.]
    assert list(loaded['Open']) == [50., 50.5, 51.]
    assert list(loaded['Volume']) == [100,200,300]  # Yahoo volume already split adjusted
    assert str(loaded.index.tz) == 'UTC'
    assert loaded['AvailableAt'].iloc[1] == pd.Timestamp('2024-11-29T18:00Z')
    assert hashlib.sha256(open(meta['path'], 'rb').read()).hexdigest() == meta['sha256']
    before = open(meta['path'], 'rb').read()
    second = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    assert meta['id'] != second['id']
    assert open(meta['path'], 'rb').read() == before


def test_early_close_excludes_incomplete_candle(tmp_path):
    repo = DataRepository(tmp_path)
    meta = repo.ingest('AAPL', history(), now='2024-11-29T17:59Z')
    assert len(repo.load(meta['id'])) == 1
    assert meta['excluded_incomplete'] == 2
    assert session_close('2024-11-29') == pd.Timestamp('2024-11-29T18:00Z')
    meta = repo.ingest('AAPL', history(), now='2024-11-29T18:00Z')
    assert len(repo.load(meta['id'])) == 2


@pytest.mark.parametrize('failure', ['duplicate','negative','nan','gap','weekend','unsorted','missing_adj'])
def test_invalid_quarantine_latest_valid_fallback(tmp_path, failure):
    repo = DataRepository(tmp_path)
    good = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    bad = history()
    if failure == 'duplicate': bad = pd.concat([bad,bad.iloc[[-1]]])
    if failure == 'negative': bad.iloc[0,bad.columns.get_loc('Close')] = -1
    if failure == 'nan': bad.iloc[0,bad.columns.get_loc('Volume')] = np.nan
    if failure == 'gap': bad = bad.iloc[[0,2]]
    if failure == 'weekend': bad.index = pd.DatetimeIndex(['2024-11-27','2024-11-30','2024-12-02'])
    if failure == 'unsorted': bad = bad.iloc[::-1]
    if failure == 'missing_adj': bad = bad.drop(columns=['Adj Close'])
    invalid = repo.ingest('AAPL', bad, now='2024-12-03T00:00Z')
    assert invalid['status'] == 'quarantined'
    assert invalid['errors']
    latest = repo.latest('AAPL')
    assert latest['id'] == good['id']
    assert latest['stale'] is True
    assert latest['latest_attempt']['id'] == invalid['id']
    with pytest.raises(ValueError): repo.load(invalid['id'])


def test_corruption_is_detected(tmp_path):
    repo = DataRepository(tmp_path)
    meta = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    with open(meta['path'], 'a') as handle: handle.write('corruption')
    with pytest.raises(ValueError, match='checksum'): repo.load(meta['id'])


def test_no_valid_snapshot_explicit_failure(tmp_path):
    repo = DataRepository(tmp_path)
    bad = repo.ingest('AAPL', pd.DataFrame(), now='2024-12-03T00:00Z')
    assert repo.latest('AAPL')['status'] == 'quarantined'
    assert repo.latest('AAPL')['stale']


def test_old_history_is_stale_and_corruption_falls_back(tmp_path):
    repo = DataRepository(tmp_path)
    old = repo.ingest('AAPL', history().iloc[:2], now='2024-12-03T00:00Z')
    assert old['stale']
    fresh = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    assert not fresh['stale']
    with open(fresh['raw_path'], 'a') as handle: handle.write('tampered raw input')
    latest = repo.latest('AAPL')
    assert latest['id'] == old['id']
    assert latest['stale']
    assert latest['latest_attempt']['status'] == 'quarantined'
    assert 'checksum' in latest['latest_attempt']['errors'][0]


def test_refresh_failure_is_visible_and_preserves_valid(tmp_path, monkeypatch):
    import yfinance
    repo = DataRepository(tmp_path)
    valid = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    class FailingTicker:
        def __init__(self, symbol): pass
        def history(self, **kwargs):
            assert kwargs['auto_adjust'] is False
            assert kwargs['actions'] is True
            raise ConnectionError('Offline')
    monkeypatch.setattr(yfinance, 'Ticker', FailingTicker)
    failure = repo.refresh('AAPL', now='2024-12-03T00:00Z')
    assert failure['status'] == 'quarantined'
    assert repo.latest('AAPL')['id'] == valid['id']
    assert repo.latest('AAPL')['stale']


def test_latest_freshness_ages_without_mutating_snapshot(tmp_path):
    repo = DataRepository(tmp_path)
    metadata = repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    manifest = next(tmp_path.glob('AAPL/*/metadata.json'))
    original = manifest.read_bytes()
    assert not repo.latest('AAPL', now='2024-12-03T20:59Z')['stale']
    aged = repo.latest('AAPL', now='2024-12-03T21:00Z')
    assert aged['stale']
    assert aged['expected_latest_session'] == '2024-12-03T00:00:00+00:00'
    assert manifest.read_bytes() == original
    assert repo.list_snapshots('AAPL')[0]['stale'] is False
    assert repo.load(metadata['id']).index[-1] == pd.Timestamp('2024-12-02', tz='UTC')


def test_latest_freshness_defaults_to_current_clock(tmp_path, monkeypatch):
    import stock_app.research.data as data_module
    repo = DataRepository(tmp_path)
    repo.ingest('AAPL', history(), now='2024-12-03T00:00Z')
    original = data_module.utc_timestamp
    monkeypatch.setattr(data_module, 'utc_timestamp', lambda value=None: original('2024-12-03T21:00Z' if value is None else value))
    assert repo.latest('AAPL')['stale']


@pytest.mark.parametrize('symbol', ['.', '..', ' . ', ' .. '])
def test_reject_relative_directory_symbols(tmp_path, symbol):
    repo = DataRepository(tmp_path)
    with pytest.raises(ValueError, match='Invalid symbol'):
        repo.ingest(symbol, history(), now='2024-12-03T00:00Z')
    with pytest.raises(ValueError, match='Invalid symbol'):
        repo.list_snapshots(symbol)
    with pytest.raises(ValueError, match='Invalid symbol'):
        repo.latest(symbol)
    assert list(tmp_path.iterdir()) == []
