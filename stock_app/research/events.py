"""Validate optional user imports with explicit provenance and availability.

Dates describing the event or reporting period do not imply when information
was known. Unverified imports remain viewable but are excluded from training.
"""
import csv
import hashlib
import json
import math
from pathlib import Path
from uuid import uuid4

import pandas as pd
from .calendar import utc_timestamp


def validate_event_records(records, *, kind='events'):
    if kind not in ('events', 'fundamentals'):
        raise ValueError('kind must be events or fundamentals')
    frame = pd.DataFrame(records).copy()
    date_field = 'event_date' if kind == 'events' else 'report_date'
    required = ('symbol', date_field, 'available_at')
    for column in required:
        if column not in frame:
            raise ValueError(f'{column} required for every imported record')
        if frame[column].isna().any():
            raise ValueError(f'{column} cannot be missing')
    for value in frame['available_at']:
        try:
            stamp = pd.Timestamp(value)
        except (TypeError, ValueError) as error:
            raise ValueError('Invalid available_at') from error
        if stamp.tzinfo is None:
            raise ValueError('available_at must include an explicit timezone')
    for column in (date_field, 'available_at'):
        try:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors='raise')
        except (TypeError, ValueError) as error:
            raise ValueError(f'Invalid {column}') from error
        if frame[column].isna().any():
            raise ValueError(f'{column} cannot be missing')
    frame['symbol'] = frame.symbol.astype(str).str.strip().str.upper()
    if not frame.symbol.str.fullmatch(r'[A-Z0-9.^=-]{1,20}').all():
        raise ValueError('Invalid symbol')
    if 'verified' not in frame:
        frame['verified'] = False
    if not frame.verified.map(lambda value: isinstance(value, bool)).all():
        raise ValueError('verified must contain explicit booleans')
    if 'source' not in frame:
        frame['source'] = ''
    if (frame.verified & (frame.source.isna() | frame.source.astype(str).str.strip().eq(''))).any():
        raise ValueError('Verified records require a source')
    if kind == 'fundamentals' and (frame.available_at.dt.normalize() < frame.report_date.dt.normalize()).any():
        raise ValueError('Fundamental available_at cannot precede report_date')
    if 'data_vintage' in frame and (frame.verified & frame.data_vintage.eq('current-vintage')).any():
        raise ValueError('Unverified current-vintage observations cannot become historical training data')
    frame['training_eligible'] = frame.verified
    frame['kind'] = kind
    return frame.sort_values('available_at', kind='stable').reset_index(drop=True)


def training_event_records(frame, *, as_of=None):
    eligible = frame['verified'].eq(True) & frame['training_eligible'].eq(True)
    if 'data_vintage' in frame:
        eligible &= frame['data_vintage'].ne('current-vintage')
    if as_of is not None:
        eligible &= pd.to_datetime(frame['available_at'], utc=True) <= utc_timestamp(as_of)
    return frame.loc[eligible].copy()


def _finite_values(values):
    """Keep missing Yahoo numeric observations null, never zero or an estimate."""
    result = {}
    for name, value in values.items():
        if value is None or pd.isna(value):
            result[str(name)] = None
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f'Non-numeric provider value for {name}') from error
        result[str(name)] = numeric if math.isfinite(numeric) else None
    return result


def download_free_context(symbol, *, provider=None, now=None):
    """Observe Yahoo earnings and quarterly statements without PIT claims.

    ``provider`` is a callable accepting a symbol and returning a ticker with
    yfinance's get_earnings_dates, get_income_stmt, get_balance_sheet and
    get_cash_flow methods. It defaults to the installed yfinance.Ticker.
    ``now`` injects a UTC observation-completion clock for deterministic tests.

    Return a JSON-safe observation bundle with events/fundamentals lists. Each
    download is a new observation snapshot; record IDs identify that snapshot's
    rows. Statement column dates are period_end, never claimed report dates.
    All free-source records are view-only and permanently training-ineligible.
    Provider failures are recorded per section and do not erase other sections.
    """
    from .data import _symbol
    symbol = _symbol(symbol)
    snapshot_id = uuid4().hex
    sections, errors = {}, []
    if provider is None:
        import yfinance as yf
        provider = yf.Ticker
    try:
        ticker = provider(symbol)
    except Exception as error:
        ticker = None
        errors.append({'section': 'provider', 'error': f'{type(error).__name__}: {error}'})
    requests = (
        ('earnings', 'get_earnings_dates', {'limit': 12}),
        ('income_statement', 'get_income_stmt', {'freq': 'quarterly'}),
        ('balance_sheet', 'get_balance_sheet', {'freq': 'quarterly'}),
        ('cash_flow', 'get_cash_flow', {'freq': 'quarterly'}),
    )
    if ticker is not None:
        for section, method, options in requests:
            try:
                frame = getattr(ticker, method)(**options)
                if frame is None or (isinstance(frame, pd.DataFrame) and frame.empty):
                    raise ValueError('No records returned by provider')
                if not isinstance(frame, pd.DataFrame) or not frame.index.is_unique or not frame.columns.is_unique:
                    raise ValueError('Provider must return a DataFrame with unique rows and columns')
                records = []
                if section == 'earnings':
                    for date, row in frame.iterrows():
                        stamp = pd.Timestamp(date)
                        if pd.isna(stamp):
                            raise ValueError('Missing earnings event date')
                        records.append({'kind': 'events', 'event_type': 'earnings',
                                        'event_date': stamp.isoformat(),
                                        'values': _finite_values(row.reindex(
                                            ['EPS Estimate', 'Reported EPS', 'Surprise(%)']))})
                else:
                    for date, values in frame.items():
                        stamp = pd.Timestamp(date)
                        if pd.isna(stamp):
                            raise ValueError('Missing financial period end')
                        records.append({'kind': 'fundamentals', 'statement': section,
                                        'period_end': stamp.date().isoformat(), 'report_date': None,
                                        'report_date_status': 'not supplied by free provider',
                                        'frequency': 'quarterly', 'values': _finite_values(values)})
                sections[section] = records
            except Exception as error:
                errors.append({'section': section, 'error': f'{type(error).__name__}: {error}'})
    # Completion is the first defensible availability bound for this download.
    downloaded_at = utc_timestamp(now).isoformat()
    result = {'id': snapshot_id, 'symbol': symbol, 'source': 'yahoo via yfinance',
              'downloaded_at': downloaded_at, 'available_at': downloaded_at,
              'data_vintage': 'current-vintage', 'events': [], 'fundamentals': [], 'errors': errors,
              'note': 'Free current-vintage observations are view-only; historical publication times and restatement vintages are unverified.'}
    for section, records in sections.items():
        for row in records:
            identity = hashlib.sha256(json.dumps([snapshot_id, section, row], sort_keys=True).encode()).hexdigest()
            row.update(id=identity, symbol=symbol, context_snapshot_id=snapshot_id,
                       source='yahoo via yfinance', data_vintage='current-vintage',
                       downloaded_at=downloaded_at, available_at=downloaded_at,
                       verified=False, training_eligible=False)
            result[row['kind']].append(row)
    has_records = bool(result['events'] or result['fundamentals'])
    result['status'] = 'partial' if errors and has_records else 'failed' if errors else 'completed'
    return result


def import_point_in_time(path, *, kind='events', now=None):
    """Read user-supplied JSON/CSV; eligibility requires explicit verification.

    JSON must be an array of records. CSV verified values must be true/false.
    Both require symbol, event_date/report_date, timezone-aware available_at,
    and a source for verified rows. Missing verified defaults to false. Import
    time and file checksum are added without replacing historical availability.
    """
    path = Path(path)
    content = path.read_bytes()
    if path.suffix.lower() == '.json':
        records = json.loads(content.decode('utf-8-sig'))
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError('Point-in-time JSON must be an array of record objects')
    elif path.suffix.lower() == '.csv':
        records = list(csv.DictReader(content.decode('utf-8-sig').splitlines()))
        for row in records:
            if 'verified' in row:
                value = row['verified'].strip().lower()
                if value not in {'true', 'false'}:
                    raise ValueError('CSV verified must be true or false')
                row['verified'] = value == 'true'
    else:
        raise ValueError('Point-in-time import requires a JSON or CSV file')
    frame = validate_event_records(records, kind=kind)
    frame['import_sha256'] = hashlib.sha256(content).hexdigest()
    frame['imported_at'] = utc_timestamp(now).isoformat()
    return frame
