"""Forecast issuance is immutable; outcome corrections retain their provenance."""

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
                   horizon=5, force_replay=False, target_definition=None, frequency=None,
                   security_id=None):
    from .forecast_identity import decision_identity, model_contract
    from .forecast_store import append
    issued = pd.Timestamp(issued_at or utcnow())
    if issued.tzinfo is None or pd.isna(issued):
        raise ValueError('Issuance time must include timezone')
    issued = issued.tz_convert('UTC')
    close, next_open, target, _ = session_times(origin, horizon)
    day = pd.Timestamp(origin).date().isoformat()
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            model = store._get(db, 'models', model_id)
        except KeyError:
            model = None
        identity = decision_identity(symbol=symbol, model_id=model_id, origin=origin,
                                     horizon=horizon, payload=payload, model=model,
                                     target_definition=target_definition, frequency=frequency,
                                     security_id=security_id)
        # Do not let new writes silently precede legacy evidence for the same decision.
        pending = db.execute("SELECT 1 FROM records r WHERE kind='forecasts' AND model_id=? AND symbol=? "
                             "AND json_extract(document,'$.origin')=? AND NOT EXISTS "
                             "(SELECT 1 FROM forecast_migration m WHERE m.legacy_id=r.id AND m.status='mapped') LIMIT 1",
                             (model_id, symbol, day)).fetchone()
        if pending:
            raise ValueError('Legacy origin requires migration/reconciliation before new issuance')
        record = {'symbol': symbol, 'model_id': model_id, 'snapshot_id': snapshot_id,
                  'origin': day, 'target': target.date().isoformat(), 'horizon': horizon,
                  'issued_at': issued.isoformat(), 'created_at': utcnow(),
                  'kind': 'prospective' if close <= issued < next_open and not force_replay else 'historical_replay',
                  'state': 'pending', 'payload': payload}
        sources = {}
        for source, snapshot in (payload.get('snapshots') or {symbol: snapshot_id}).items():
            try:
                dataset = store._get(db, 'datasets', snapshot)
            except KeyError:
                continue
            sources[source] = {key: dataset[key] for key in
                               ('id', 'sha256', 'raw_sha256', 'created_at', 'downloaded_at', 'last_session') if key in dataset}
        provenance = {'model_artifact_sha256': identity['model_artifact_sha256'],
                      'feature_fingerprint': model_contract(model)['feature_fingerprint'], 'sources': sources}
        return append(db, identity, record, provenance=provenance)


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
        outcome = {'log_return': actual, 'event': int(actual > record.get('identity', {}).get('target_definition', {}).get('event_threshold', .002)),
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


def cached_forecast(store, model, *, symbol, snapshot_id, origin, snapshots):
    """Reuse exact, verified outputs without fitting or recomputing a prediction."""
    from .forecast_identity import decision_identity, digest, model_contract, prediction_content
    from .forecast_store import project
    from .lifecycle import verify_artifact
    import json
    if not model.get('artifact') or not model.get('artifact_sha256'):
        return None
    contract = model_contract(model)
    horizon = contract['horizon'] or 5
    task = contract['task'] or model.get('task')
    hint = {'probability': 0} if task == 'binary' else {'predicted_return': 0}
    identity = decision_identity(symbol=symbol, model_id=model['id'], origin=origin, horizon=horizon,
                                 payload=hint, model=model)
    issuance_id = 'fc-' + digest(identity)
    input_key = digest({'snapshot_id': snapshot_id, 'snapshots': snapshots, 'input_sha256': None})
    with store.connection() as db:
        row = db.execute('''SELECT i.document,r.document,r.output_hash FROM forecast_issuances i
                            JOIN forecast_revisions r ON r.issuance_id=i.id
                            WHERE i.id=? AND r.input_key=?''', (issuance_id, input_key)).fetchone()
    if row is None:
        return None
    revision = json.loads(row[1])
    if digest(prediction_content(revision['payload'])) != row[2]:
        raise ValueError('Cached revision output checksum mismatch')
    # A cached output never bypasses current artifact-integrity checks.
    verify_artifact(model)
    return project(json.loads(row[0]), revision, requested=True)
