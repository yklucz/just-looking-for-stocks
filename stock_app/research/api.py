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
    items = engine.store.list(kind, symbol=request.args.get('symbol') or request.args.get('ticker'),
                              model_id=request.args.get('model_id'))
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
