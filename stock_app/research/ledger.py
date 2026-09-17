"""Forecast issuance is immutable; outcome corrections retain their provenance."""
import hashlib
import json

import exchange_calendars as xcals
import numpy as np
import pandas as pd

from .store import utcnow


def session_times(origin, horizon=5):
    day = pd.Timestamp(origin).tz_localize(None).normalize()
    calendar = xcals.get_calendar('XNYS', start=day - pd.Timedelta(days=10),
                                  end=day + pd.Timedelta(days=horizon * 4 + 30))
    if not calendar.is_session(day):
        raise ValueError('Forecast origin must be a trading session')
    target = calendar.session_offset(day, horizon)
    return calendar.session_close(day), calendar.session_open(calendar.next_session(day)), target, calendar.session_close(target)


def issue_forecast(store, *, symbol, model_id, snapshot_id, origin, payload, issued_at=None,
                   horizon=5, force_replay=False):
    issued = pd.Timestamp(issued_at or utcnow())
    if issued.tzinfo is None:
        raise ValueError('Issuance time must include timezone')
    close, next_open, target, _ = session_times(origin, horizon)
    day = pd.Timestamp(origin).date().isoformat()
    identity = hashlib.sha256(json.dumps([model_id, snapshot_id, day, horizon]).encode()).hexdigest()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            return store._get(db, 'forecasts', identity)
        except KeyError:
            pass
        previous = db.execute("SELECT id FROM records WHERE kind='forecasts' AND model_id=? "
                              "AND json_extract(document,'$.origin')=? ORDER BY created_at DESC LIMIT 1",
                              (model_id, day)).fetchone()
        record = {'id': identity, 'symbol': symbol, 'model_id': model_id, 'snapshot_id': snapshot_id,
                  'origin': day, 'target': target.date().isoformat(), 'horizon': horizon,
                  'issued_at': issued.isoformat(), 'kind': 'prospective' if close <= issued < next_open
                  and not force_replay else 'historical_replay', 'state': 'pending',
                  'payload': payload, 'revision_of': previous[0] if previous else None}
        return store._put(db, 'forecasts', record)


def resolve_forecasts(store, symbol, history, *, snapshot_id, now=None):
    clock = pd.Timestamp(now or utcnow())
    indexed = history.copy()
    indexed.index = pd.to_datetime(indexed.index, utc=True).normalize()
    for record in store.list('forecasts', symbol=symbol):
        _, _, target, target_close = session_times(record['origin'], record['horizon'])
        if clock < target_close:
            continue
        origin = pd.Timestamp(record['origin'], tz='UTC')
        target = pd.Timestamp(target.date(), tz='UTC')
        calendar = xcals.get_calendar('XNYS', start=origin.tz_localize(None) - pd.Timedelta(days=2),
                                      end=target.tz_localize(None) + pd.Timedelta(days=2))
        required = pd.to_datetime(calendar.sessions_in_range(origin.tz_localize(None), target.tz_localize(None)), utc=True)
        if not required.isin(indexed.index).all():
            store.update('forecasts', record['id'], state='missing_data')
            continue
        values = indexed.loc[[origin, target], 'Close'].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values <= 0).any():
            store.update('forecasts', record['id'], state='missing_data')
            continue
        actual = float(np.log(values[1] / values[0]))
        outcome = {'log_return': actual, 'event': int(actual > .002),
                   'price_on_issue_basis': float(record['payload']['origin_close'] * np.exp(actual)),
                   'snapshot_id': snapshot_id, 'resolved_at': clock.isoformat()}
        previous = record.get('outcome')
        if previous and abs(previous['log_return'] - actual) <= 1e-12:
            if record['state'] == 'missing_data':
                store.update('forecasts', record['id'], state='corrected' if record.get('outcome_revisions') else 'resolved')
            continue
        changes = {'outcome': outcome, 'state': 'corrected' if previous else 'resolved'}
        if previous:
            changes['outcome_revisions'] = record.get('outcome_revisions', []) + [previous]
        store.update('forecasts', record['id'], **changes)
