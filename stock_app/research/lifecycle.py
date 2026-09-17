"""Frozen shadow nominations and paired prospective model qualification."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .store import utcnow


def nominate(store, identity):
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        model = store._get(db, 'models', identity)
        if model['state'] == 'shadow':
            return model
        if model['state'] != 'candidate':
            raise ValueError('Only a candidate can start a new shadow evaluation')
        rows = db.execute("SELECT document FROM records WHERE kind='models' AND symbol=? AND state='shadow'",
                          (model['symbol'],)).fetchall()
        for row in rows:
            previous = json.loads(row[0])
            if previous['task'] == model['task']:
                previous['state'] = 'rejected'
                previous['rejection_reason'] = 'Replaced shadow nomination; evidence window closed'
                store._put(db, 'models', previous)
        active = db.execute('SELECT model_id FROM active_models WHERE symbol=? AND task=?',
                            (model['symbol'], model['task'])).fetchone()
        model.update(state='shadow', nominated_at=utcnow(), comparison_model_id=active[0] if active else None)
        return store._put(db, 'models', model)


def qualification(store, identity):
    from .statistics import moving_block_bootstrap
    model = store.get('models', identity)
    result = {'eligible': False, 'resolved': 0, 'reasons': [], 'metrics': {}}
    if model['state'] != 'shadow':
        result['reasons'].append('Only a currently nominated shadow model can qualify for activation')
        return result
    if not model.get('nominated_at'):
        result['reasons'].append('Nominate a frozen candidate for shadow evaluation first')
        return result
    start = pd.Timestamp(model['nominated_at'])
    def records(model_id):
        rows = sorted(store.list('forecasts', model_id=model_id), key=lambda row: row['created_at'])
        selected = {}
        for row in rows:
            if (row.get('kind') == 'prospective' and pd.Timestamp(row['issued_at']) >= start):
                # First issued prediction wins; revisions must not select a better forecast.
                selected.setdefault(row['origin'], row)
        return {day: row for day, row in selected.items()
                if row.get('state') in {'resolved', 'corrected'} and row.get('outcome')}
    candidates = records(identity)
    comparison = model.get('comparison_model_id')
    active = store.active(model['symbol'], model['task'])
    if (active['id'] if active else None) != comparison:
        result['reasons'].append('Active comparator changed; nominate a fresh candidate')
        return result
    baseline = records(comparison) if comparison else {}
    origins = sorted(set(candidates) & set(baseline)) if comparison else sorted(candidates)
    origins = [day for day in origins if not comparison or
               np.isclose(candidates[day]['outcome']['log_return'], baseline[day]['outcome']['log_return'], atol=1e-12, rtol=0)]
    result['resolved'] = len(origins)
    if len(origins) < 126:
        result['reasons'].append('At least 126 matched resolved prospective daily origins are required')
        return result
    actual = np.array([candidates[d]['outcome']['event' if model['task'] == 'binary' else 'log_return'] for d in origins])
    key = 'probability' if model['task'] == 'binary' else 'predicted_return'
    forecast = np.array([candidates[d]['payload'][key] for d in origins])
    loss = lambda values: (values - actual) ** 2 if model['task'] == 'binary' else np.abs(values - actual)
    candidate_errors = loss(forecast)
    if model['task'] == 'binary':
        auc = float(roc_auc_score(actual, forecast)) if len(np.unique(actual)) == 2 else None
        result['metrics']['roc_auc'] = auc
        if auc is None or auc <= .5:
            result['reasons'].append('ROC-AUC must exceed 0.5 on resolved prospective outcomes')
        prior = [candidates[d]['payload'].get('training_prior') for d in origins]
        if any(p is None for p in prior):
            result['reasons'].append('Training-prior baseline is unavailable')
            return result
        references = {'training_prior': np.array(prior)}
    else:
        references = {'unchanged_price': np.zeros(len(origins))}
    if comparison:
        references['active_model'] = np.array([baseline[d]['payload'][key] for d in origins])
    result['metrics']['loss'] = float(candidate_errors.mean())
    for name, values in references.items():
        interval = moving_block_bootstrap(candidate_errors, loss(values))
        result['metrics'][name] = interval
        if interval['lower'] <= 0:
            result['reasons'].append(f'Improvement over {name} is not positive across the confidence interval')
    result['eligible'] = not result['reasons']
    return result


def verify_artifact(model):
    path = Path(model['artifact'])
    if model.get('metadata', {}).get('legacy'):
        from ..models.registry import fingerprint
        metadata = json.loads((path / 'metadata.json').read_text())
        contract = metadata['contract']
        expected_type = 'xgboost' if model['task'] == 'binary' else 'xgboost_regressor'
        if (contract['ticker'] != model['symbol'] or contract['model_type'] != expected_type
                or contract['target'] != {'task': model['task'], 'horizon': 5, 'threshold': .002}
                or fingerprint(contract) != model['metadata']['binding']['fingerprint']
                or metadata['model_file'] != 'model.json'
                or hashlib.sha256((path / 'model.json').read_bytes()).hexdigest() != model['artifact_sha256']):
            raise ValueError('Legacy artifact identity or checksum does not match')
    elif not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != model['artifact_sha256']:
        raise ValueError('Candidate artifact checksum does not match')
    else:
        metadata = json.loads(path.with_suffix('.metadata.json').read_text())
        if (metadata.get('artifact_sha256') != model['artifact_sha256']
                or metadata.get('ticker') != model['symbol'] or metadata.get('task') != model['task']
                or metadata.get('artifact_version') != 1):
            raise ValueError('Candidate metadata does not match its registered identity')


def activate(store, identity):
    def validate(model):
        if model['state'] != 'shadow':
            raise ValueError('Only a nominated shadow candidate can be activated')
        evidence = qualification(store, identity)
        if not evidence['eligible']:
            raise ValueError('; '.join(evidence['reasons']))
        verify_artifact(model)
        model['qualification_at_activation'] = evidence
    return store.set_active(identity, validate=validate)
