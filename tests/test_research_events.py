"""Provider observations never acquire historical point-in-time eligibility."""
import json

import numpy as np
import pandas as pd
import pytest

from stock_app.research.events import (
    download_free_context, import_point_in_time, training_event_records, validate_event_records,
)


class TickerFixture:
    def get_earnings_dates(self, limit=12):
        assert limit == 12
        return pd.DataFrame({'EPS Estimate': [2.1, np.nan], 'Reported EPS': [2.2, np.nan],
                             'Surprise(%)': [4.7, np.nan]},
                            index=pd.to_datetime(['2024-11-01T16:00:00-04:00', '2025-02-01T16:00:00-05:00'], utc=True))

    def get_income_stmt(self, freq):
        assert freq == 'quarterly'
        return pd.DataFrame({pd.Timestamp('2024-09-30'): [123., np.nan]}, index=['TotalRevenue', 'NetIncome'])

    def get_balance_sheet(self, freq):
        assert freq == 'quarterly'
        return pd.DataFrame({pd.Timestamp('2024-09-30'): [456.]}, index=['TotalAssets'])

    def get_cash_flow(self, freq):
        assert freq == 'quarterly'
        return pd.DataFrame({pd.Timestamp('2024-09-30'): [78.]}, index=['OperatingCashFlow'])


def test_free_provider_context_is_current_vintage_and_never_training_data():
    requested = []
    def provider(symbol):
        requested.append(symbol)
        return TickerFixture()
    result = download_free_context('aapl', provider=provider, now='2024-12-03T12:00Z')
    assert requested == ['AAPL']
    assert result['status'] == 'completed'
    assert len(result['events']) == 2
    assert len(result['fundamentals']) == 3
    records = result['events'] + result['fundamentals']
    assert len({record['id'] for record in records}) == len(records)
    for row in records:
        assert row['verified'] is False and row['training_eligible'] is False
        assert row['available_at'] == row['downloaded_at'] == result['downloaded_at']
        assert row['context_snapshot_id'] == result['id']
    assert result['events'][0]['event_date'].startswith('2024-11-01')
    assert result['events'][1]['values']['Reported EPS'] is None
    for row in result['fundamentals']:
        assert row['period_end'] == '2024-09-30'
        assert row['report_date'] is None
    assert len(training_event_records(pd.DataFrame(records), as_of='2025-03-01T00:00Z')) == 0
    json.dumps(result, allow_nan=False)


def test_partial_provider_failure_keeps_other_sections_visible():
    class Partial(TickerFixture):
        def get_earnings_dates(self, limit=12):
            raise ConnectionError('Provider unavailable')
        def get_balance_sheet(self, freq):
            return None
    result = download_free_context('AAPL', provider=lambda _: Partial(), now='2024-12-03T12:00Z')
    assert result['status'] == 'partial'
    assert result['events'] == []
    assert len(result['fundamentals']) == 2
    assert {error['section'] for error in result['errors']} == {'earnings', 'balance_sheet'}


def test_provider_creation_failure_is_explicit():
    def provider(_):
        raise ConnectionError('Offline')
    result = download_free_context('AAPL', provider=provider, now='2024-12-03T12:00Z')
    assert result['status'] == 'failed'
    assert result['events'] == result['fundamentals'] == []
    assert result['errors'][0]['section'] == 'provider'


@pytest.mark.parametrize('available_at', ['2024-11-02', '2024-11-02T10:00:00'])
def test_import_availability_must_have_timezone(available_at):
    with pytest.raises(ValueError, match='timezone'):
        validate_event_records([dict(symbol='AAPL', event_date='2024-11-01', available_at=available_at)])


def test_json_point_in_time_import_retains_original_availability(tmp_path):
    path = tmp_path / 'filings.json'
    path.write_text(json.dumps([dict(symbol='AAPL', report_date='2024-11-01', available_at='2024-11-01T16:30:00-04:00',
                                     verified=True, source='issuer filing', revenue=123)]))
    frame = import_point_in_time(path, kind='fundamentals', now='2024-12-03T12:00Z')
    assert frame.available_at.iloc[0] == pd.Timestamp('2024-11-01T20:30Z')
    assert frame.imported_at.iloc[0] == '2024-12-03T12:00:00+00:00'
    assert len(frame.import_sha256.iloc[0]) == 64
    assert len(training_event_records(frame, as_of='2024-11-01T20:29Z')) == 0
    assert len(training_event_records(frame, as_of='2024-11-01T20:30Z')) == 1


def test_csv_import_requires_explicit_boolean_and_source(tmp_path):
    path = tmp_path / 'events.csv'
    path.write_text('symbol,event_date,available_at,verified,source\nAAPL,2024-11-01,2024-10-20T12:00Z,true,issuer calendar\n')
    frame = import_point_in_time(path, kind='events')
    assert bool(frame.training_eligible.iloc[0])
    path.write_text(path.read_text().replace(',true,', ',yes,'))
    with pytest.raises(ValueError, match='verified'):
        import_point_in_time(path, kind='events')


def test_free_record_cannot_be_relabelled_verified_for_historical_training():
    result = download_free_context('AAPL', provider=lambda _: TickerFixture(), now='2024-12-03T12:00Z')
    row = dict(result['events'][0], verified=True)
    with pytest.raises(ValueError, match='current-vintage'):
        validate_event_records([row])


def test_earnings_text_metadata_does_not_break_numeric_observations():
    class TextEarnings(TickerFixture):
        def get_earnings_dates(self, limit=12):
            frame = super().get_earnings_dates(limit)
            frame['Event Name'] = 'Quarterly results'
            return frame
    result = download_free_context('AAPL', provider=lambda _: TextEarnings(), now='2024-12-03T12:00Z')
    assert result['status'] == 'completed'
    assert result['events'][0]['values']['Reported EPS'] == 2.2
    assert 'Event Name' not in result['events'][0]['values']
