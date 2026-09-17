"""Immutable auditable daily snapshots, with visible quarantine and valid fallback.

Yahoo's unadjusted download is already split-normalized in prices and volume.
Adj Close / Close adds its corporate-action return adjustment to OHLC. Volume
remains Yahoo's split-adjusted share volume; dividends never rescale shares.
This is a current-vintage data snapshot, not point-in-time corporate-action data.
"""
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd

from .calendar import session_index, session_close, sessions_between, utc_timestamp, latest_completed_session

PRICE_COLUMNS = ('Open', 'High', 'Low', 'Close')
RAW_COLUMNS = (*PRICE_COLUMNS, 'Adj Close', 'Volume', 'Dividends', 'Stock Splits')


def _symbol(value):
    symbol = str(value).strip().upper()
    if symbol in {'.', '..'} or not re.fullmatch(r'[A-Z0-9.^=-]{1,20}', symbol):
        raise ValueError('Invalid symbol')
    return symbol


def _checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DataRepository:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def ingest(self, symbol, frame, *, now=None, source='yahoo', exchange='XNYS'):
        symbol = _symbol(symbol)
        now = utc_timestamp(now)
        snapshot_id = uuid4().hex
        directory = self.root / symbol / snapshot_id
        directory.mkdir(parents=True, exist_ok=False)
        raw_path = directory / 'raw.csv'
        raw = frame.copy() if isinstance(frame, pd.DataFrame) else pd.DataFrame()
        # Save original input before validation, preserving rejected evidence.
        raw.to_csv(raw_path, index_label='Session')
        meta = dict(id=snapshot_id, symbol=symbol, source=str(source), exchange=exchange,
                    timezone='America/New_York',
                    download_settings={'auto_adjust': False, 'actions': True, 'interval': '1d'},
                    created_at=now.isoformat(), recorded_at=utc_timestamp().isoformat(),
                    status='quarantined', stale=True, errors=[], path=None, sha256=None,
                    raw_path=str(raw_path), raw_sha256=_checksum(raw_path),
                    excluded_incomplete=0, rows=0, adjustment='Adj Close / Close for OHLC; Yahoo split-adjusted Volume unchanged',
                    data_vintage='current snapshot; not point-in-time corporate-action history',
                    calendar_version=__import__('exchange_calendars').__version__)
        try:
            adjusted, excluded = self._validate_and_adjust(raw, now, exchange)
            path = directory / 'adjusted.csv'
            adjusted.to_csv(path, index_label='Session')
            expected_latest = latest_completed_session(now, exchange)
            meta.update(status='valid', stale=adjusted.index[-1] < expected_latest, path=str(path), sha256=_checksum(path),
                        excluded_incomplete=excluded, rows=len(adjusted),
                        first_session=adjusted.index[0].isoformat(), last_session=adjusted.index[-1].isoformat(),
                        start=adjusted.index[0].isoformat(), end=adjusted.index[-1].isoformat(),
                        expected_latest_session=expected_latest.isoformat(),
                        feature_available_at=adjusted['AvailableAt'].iloc[-1].isoformat())
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            meta['errors'] = [str(error)]
        manifest = directory / 'metadata.pending'
        manifest.write_text(json.dumps(meta, indent=2), encoding='utf-8')
        manifest.rename(directory / 'metadata.json')
        return meta

    @staticmethod
    def _validate_and_adjust(raw, now, exchange):
        if raw.empty:
            raise ValueError('Empty history')
        if not raw.columns.is_unique or not set(RAW_COLUMNS).issubset(raw.columns):
            raise ValueError('Unique OHLCV, Adj Close, Dividends and Stock Splits columns required')
        if not isinstance(raw.index, pd.DatetimeIndex):
            raise ValueError('Daily history must have a DatetimeIndex')
        frame = raw.loc[:, RAW_COLUMNS].copy()
        frame.index = session_index(frame.index)
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError('Sessions must be sorted and unique')
        for column in RAW_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors='raise')
        if not np.isfinite(frame.to_numpy(dtype=float)).all():
            raise ValueError('OHLCV and corporate actions must be finite')
        if (frame[list(PRICE_COLUMNS) + ['Adj Close']] <= 0).any().any():
            raise ValueError('Prices must be positive')
        if (frame[['Volume', 'Dividends', 'Stock Splits']] < 0).any().any():
            raise ValueError('Volume and corporate actions cannot be negative')
        if ((frame.High < frame[['Open','Close','Low']].max(axis=1)) |
                (frame.Low > frame[['Open','Close','High']].min(axis=1))).any():
            raise ValueError('Inconsistent OHLC price bounds')
        closes = pd.Series([session_close(day, exchange) for day in frame.index], index=frame.index)
        complete = closes <= now
        excluded = int((~complete).sum())
        frame = frame.loc[complete].copy()
        if frame.empty:
            raise ValueError('No completed exchange sessions')
        expected = sessions_between(frame.index[0], frame.index[-1], exchange)
        missing = expected.difference(frame.index)
        if len(missing):
            raise ValueError('Missing exchange sessions: ' + ', '.join(day.date().isoformat() for day in missing))
        factor = frame['Adj Close'] / frame['Close']
        frame.loc[:, list(PRICE_COLUMNS)] = frame.loc[:, list(PRICE_COLUMNS)].mul(factor, axis=0)
        frame['AvailableAt'] = closes.loc[complete]
        return frame, excluded

    def list_snapshots(self, symbol=None):
        base = self.root if symbol is None else self.root / _symbol(symbol)
        records = []
        for path in base.glob('*/metadata.json' if symbol is not None else '*/*/metadata.json'):
            try:
                records.append(json.loads(path.read_text(encoding='utf-8')))
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda item: (item.get('recorded_at', item['created_at']), item['id']), reverse=True)

    def load(self, snapshot_id):
        meta = next((item for item in self.list_snapshots() if item['id'] == snapshot_id), None)
        if meta is None:
            raise KeyError(f'Unknown snapshot: {snapshot_id}')
        if meta['status'] != 'valid':
            raise ValueError('Quarantined snapshot is unavailable for research')
        for key, checksum in (('path','sha256'), ('raw_path','raw_sha256')):
            path = Path(meta[key]).resolve()
            if not path.is_relative_to(self.root) or not path.is_file() or _checksum(path) != meta[checksum]:
                raise ValueError('Snapshot checksum verification failed')
        frame = pd.read_csv(meta['path'], index_col='Session', parse_dates=['Session'])
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame['AvailableAt'] = pd.to_datetime(frame['AvailableAt'], utc=True)
        return frame

    def latest(self, symbol, *, now=None):
        clock = utc_timestamp(now)
        records = self.list_snapshots(symbol)
        if not records:
            raise KeyError(f'No snapshots for {_symbol(symbol)}')
        attempt = records[0]
        for item in records:
            if item['status'] != 'valid':
                continue
            try:
                self.load(item['id'])
            except (ValueError, OSError):
                if item['id'] == attempt['id']:
                    attempt = dict(attempt, status='quarantined', stale=True,
                                   errors=['Snapshot checksum verification failed'])
                continue
            result = dict(item)
            expected = latest_completed_session(clock, item.get('exchange', 'XNYS'))
            result['expected_latest_session'] = expected.isoformat()
            result['stale'] = (utc_timestamp(item['last_session']) < expected
                               or item.get('stale', False) or item['id'] != attempt['id'])
            if result['stale']:
                result['latest_attempt'] = attempt
            return result
        result = dict(attempt, stale=True)
        if result['status'] == 'valid':
            result.update(status='quarantined', errors=['No checksum-valid snapshot available'])
        return result

    def refresh(self, symbol, now=None):
        import yfinance as yf
        try:
            frame = yf.Ticker(_symbol(symbol)).history(period='max', interval='1d', auto_adjust=False, actions=True)
        except Exception as error:
            frame = pd.DataFrame()
            meta = self.ingest(symbol, frame, now=now, source=f'yahoo download failed: {type(error).__name__}')
            return meta
        return self.ingest(symbol, frame, now=now)
