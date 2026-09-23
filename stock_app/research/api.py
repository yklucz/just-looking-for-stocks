"""Local research interfaces; the host app enforces same-origin mutations."""
import csv
import io
import json

from flask import Blueprint, Response, current_app, jsonify, request

from .runtime import get_runtime

api = Blueprint('research', __name__, url_prefix='/api/research')


@api.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error), code='INVALID_REQUEST'), 400


@api.errorhandler(KeyError)
def missing(error):
    return jsonify(error='Research record not found', code='NOT_FOUND'), 404


@api.errorhandler(OSError)
def unavailable(error):
    return jsonify(error=str(error), code='ARTIFACT_UNAVAILABLE'), 503


def runtime():
    value = get_runtime()
    if current_app.config.get('RESEARCH_BACKGROUND', not current_app.testing):
        value.start()
    return value


@api.get('/<kind>')
def records(kind):
    if kind not in {'datasets', 'jobs', 'models', 'forecasts', 'events'}:
        raise KeyError(kind)
    engine = runtime()
    filters = {'symbol': request.args.get('symbol') or request.args.get('ticker'),
               'model_id': request.args.get('model_id')}
    if kind == 'forecasts':
        from .forecast_store import list_issuances
        with engine.store.connection() as db:
            items = list_issuances(db, **filters)
    else:
        items = engine.store.list(kind, **filters)
    if kind == 'datasets':
        import pandas as pd
        from .calendar import latest_completed_session
        latest = latest_completed_session().date()
        for item in items:
            end = item.get('end') or item.get('last_session')
            item['stale'] = bool(item.get('stale') or not end or pd.Timestamp(end).date() < latest)
    if kind == 'jobs':
        for item in items:
            addendum = engine.root / 'experiments' / item['id'] / 'feature-comparison-v1.json'
            if addendum.exists():
                comparison = json.loads(addendum.read_text())
                item['feature_comparison'] = {
                    key: comparison.get(key) for key in ('schema_version', 'status', 'aggregate',
                                                          'source_report_sha256', 'selection_rule')}
                item['feature_comparison']['folds'] = [
                    {'fold': fold['fold'], 'paired_loss': fold['feature_comparison']['paired_loss']}
                    for fold in comparison.get('folds', []) if fold.get('feature_comparison')]
            progress = engine.root / 'experiments' / item['id'] / 'progress.json'
            if item['state'] in {'running', 'paused'} and progress.exists():
                completed = json.loads(progress.read_text()).get('completed_fits', [])
                item['progress'] = {**item.get('progress', {}), 'stage': f'{len(completed)} completed fits'}
    if kind == 'models':
        from .lifecycle import qualification
        items = [{**item, 'qualification': qualification(engine.store, item['id'])} for item in items]
    if request.args.get('kind'):
        items = [item for item in items if item.get('kind') == request.args['kind']]
    if request.args.get('from') or request.args.get('to'):
        from datetime import date
        def day(value):
            return date.fromisoformat(str(value)[:10])
        start = day(request.args['from']) if request.args.get('from') else None
        end = day(request.args['to']) if request.args.get('to') else None
        if start and end and start > end:
            raise ValueError('Period start must not follow period end')
        def overlaps(item):
            if kind == 'jobs' and item.get('kind') == 'experiment':
                folds = item.get('progress', {}).get('folds', [])
                periods = [fold.get('boundaries', {}).get('test', {}) for fold in folds]
                periods = [(p.get('origin_start'), p.get('origin_end')) for p in periods]
            elif kind == 'forecasts':
                periods = [(item.get('origin'), item.get('origin'))]
            elif kind == 'datasets':
                periods = [(item.get('start'), item.get('end'))]
            else:
                periods = [(item.get('created_at'), item.get('created_at'))]
            return any(a and b and (not start or day(b) >= start) and (not end or day(a) <= end)
                       for a, b in periods)
        items = [item for item in items if overlaps(item)]
    if request.args.get('format') == 'csv':
        buffer = io.StringIO()
        fields = sorted({key for item in items for key in item})
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        for item in items:
            safe = {}
            for key, value in item.items():
                value = json.dumps(value) if isinstance(value, (dict, list)) else str(value if value is not None else '')
                safe[key] = "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
            writer.writerow(safe)
        return Response(buffer.getvalue(), mimetype='text/csv', headers={
            'Content-Disposition': f'attachment; filename="{kind}.csv"'})
    return jsonify(items=items)


@api.get('/forecasts/<identity>/revisions')
def forecast_revisions(identity):
    from .forecast_store import get, revisions
    with get_runtime().store.connection() as db:
        get(db, identity)
        return jsonify(items=revisions(db, identity))


@api.get('/forecast-ledger/audit')
def forecast_audit():
    from .forecast_audit import audit_forecasts
    from .forecast_admin import database_path
    return jsonify(audit_forecasts(database_path()))


@api.get('/summary')
def summary():
    from .statistics import equal_weight_aggregate
    latest = {}
    for job in runtime().store.list('jobs', state='completed'):
        if job['kind'] == 'experiment' and job.get('progress', {}).get('aggregate'):
            latest.setdefault((job['symbol'], job['parameters']['task']), job)
    groups = {}
    for task in ('binary', 'regression'):
        jobs = [job for (symbol, kind), job in latest.items() if kind == task]
        metrics = ['brier','log_loss','auc','balanced_accuracy'] if task == 'binary' else ['mae','rmse','interval_coverage']
        groups[task] = {'symbols': [job['symbol'] for job in jobs],
                        'per_symbol': {job['symbol']: job['progress']['aggregate'] for job in jobs},
                        'equal_weight': equal_weight_aggregate([job['progress']['aggregate'] for job in jobs], metrics),
                        'evaluation_kind': 'historical_replay',
                        'note': 'Each symbol has equal weight; latest runs may cover different periods. Dollar errors are not pooled.'}
    return jsonify(groups)


@api.post('/jobs')
def create_job():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('JSON object required')
    engine = runtime()
    job = engine.submit(data.get('kind'), data.get('symbol', ''), data.get('task', 'binary'),
                        include_gru=data.get('include_gru', False))
    return jsonify(job), 202


@api.post('/jobs/<identity>/<action>')
def control_job(identity, action):
    return jsonify(runtime().control(identity, action))


@api.post('/models/<identity>/<action>')
def control_model(identity, action):
    from .lifecycle import activate, nominate, verify_artifact
    store = runtime().store
    if action == 'nominate':
        verify_artifact(store.get('models', identity))
        return jsonify(nominate(store, identity))
    if action == 'activate':
        return jsonify(activate(store, identity))
    if action == 'rollback':
        return jsonify(store.rollback(identity, validate=verify_artifact))
    raise ValueError('Unsupported model action')


@api.post('/settings')
def settings():
    from .config import SYMBOLS
    data = request.get_json(silent=True)
    if (not isinstance(data, dict) or not isinstance(data.get('symbols'), list)
            or not all(isinstance(symbol, str) for symbol in data['symbols'])):
        raise ValueError('symbols array required')
    symbols = list(dict.fromkeys(data['symbols']))
    if not symbols or any(symbol not in SYMBOLS for symbol in symbols):
        raise ValueError('Select symbols from the configured research universe')
    return jsonify(runtime().store.put('settings', {'id': 'universe', 'symbols': symbols}))


@api.post('/events')
def import_events():
    from .events import validate_event_records
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError('JSON object required')
    if not isinstance(body.get('records'), list) or not all(isinstance(row, dict) for row in body['records']):
        raise ValueError('records must be an array of objects')
    if any('id' in row and (not isinstance(row['id'], str) or not row['id'].strip())
           for row in body['records']):
        raise ValueError('Record IDs must be non-empty strings when supplied')
    frame = validate_event_records(body.get('records', []), kind=body.get('kind', 'events'))
    rows = json.loads(frame.to_json(orient='records', date_format='iso'))
    for row in rows:
        # DataFrame alignment supplies null for an omitted optional ID.
        if row.get('id') is None:
            row.pop('id', None)
    store = runtime().store
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        items = [store._put(db, 'events', row) for row in rows]
    return jsonify(items=items), 201


@api.get('/registry/audit')
def registry_audit():
    from .registry_admin import audit
    return jsonify(audit(_registry_path()))


def _registry_path():
    import os
    from pathlib import Path
    return Path(os.environ.get('STOCK_RESEARCH_ROOT', Path(__file__).resolve().parents[2] / 'artifacts/research')) / 'research.sqlite3'


@api.get('/registry/<kind>')
def registry_records(kind):
    from .registry_cli import read_rows
    return jsonify(read_rows(_registry_path(), 'runs' if kind == 'history' else kind))


@api.get('/registry/<kind>/<identity>')
def registry_record(kind, identity):
    from .registry_cli import read_rows
    return jsonify(read_rows(_registry_path(), kind, identity))


@api.get('/integrity/runs/<run_id>/evidence')
def integrity_evidence(run_id):
    from .integrity import get_evidence
    return jsonify(get_evidence(_registry_path(), run_id))


@api.get('/integrity/runs/<run_id>/verification')
def integrity_verification(run_id):
    from .integrity import verify_run
    return jsonify(verify_run(_registry_path(), run_id, request.args.get('depth', 'metadata')))


@api.get('/integrity/runs/<run_id>/reproductions')
def integrity_reproductions(run_id):
    from .integrity_replay import reproduction_history
    return jsonify(reproduction_history(_registry_path(), run_id))


@api.get('/integrity/cases')
@api.get('/integrity/cases/<identity>')
def integrity_cases(identity=None):
    from .integrity_reconcile import list_cases
    return jsonify(list_cases(_registry_path(), identity))


@api.get('/integrity/audit')
def integrity_audit():
    from .integrity_audit import audit_integrity
    return jsonify(audit_integrity(_registry_path(), depth=request.args.get('depth', 'metadata')))


@api.get('/pit/sources')
def pit_sources():
    from .pit.store import list_rows
    return jsonify(list_rows(_registry_path(), 'sources'))


@api.get('/pit/resolve/<value>')
def pit_resolve(value):
    from .pit.store import resolve_identifier
    return jsonify(resolve_identifier(_registry_path(), value, namespace=request.args.get('namespace','ticker'), scope=request.args.get('scope'), on_date=request.args.get('on_date')))


@api.get('/pit/identities/<identity>')
def pit_identity(identity):
    from .pit.store import get, list_rows
    return jsonify(identity=get(_registry_path(),'identities',identity), identifiers=[r for r in list_rows(_registry_path(),'identifiers') if r['identity_id']==identity])


@api.get('/pit/events/<identity>')
def pit_history(identity):
    from .pit.store import get, list_rows
    return jsonify(event=get(_registry_path(),'events',identity), revisions=[r for r in list_rows(_registry_path(),'revisions') if r['event_id']==identity])


@api.get('/pit/as-of')
def pit_as_of():
    from .pit.query import get_as_of
    return jsonify(get_as_of(_registry_path(),as_of=request.args.get('as_of'),strictness=request.args.get('strictness'),identity_id=request.args.get('identity_id'),data_type=request.args.get('data_type'),source_id=request.args.get('source_id')))


@api.get('/pit/audit')
def pit_audit():
    from .pit.audit import audit
    return jsonify(audit(_registry_path(),depth=request.args.get('depth','metadata'),as_of=request.args.get('as_of')))
