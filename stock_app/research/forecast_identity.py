"""Versioned economic identity, deliberately independent of input snapshots."""
import hashlib
import json
import math

import pandas as pd


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def model_contract(model):
    """Use persisted registry contracts; never infer a historical contract from current files."""
    metadata = (model or {}).get('metadata', {})
    if metadata.get('legacy'):
        contract = metadata.get('contract', {})
        return {'task': contract.get('target', {}).get('task'),
                'horizon': contract.get('target', {}).get('horizon'),
                'threshold': contract.get('target', {}).get('threshold'),
                'frequency': contract.get('interval'), 'family': contract.get('model_type'),
                'feature_fingerprint': digest({key: contract.get(key) for key in
                    ('feature_names', 'feature_config', 'feature_version')}) if contract else None,
                'contract_fingerprint': metadata.get('binding', {}).get('fingerprint')}
    candidate = metadata.get('candidate', {})
    return {'task': candidate.get('task'), 'horizon': candidate.get('horizon'),
            'threshold': candidate.get('event_threshold'),
            'frequency': candidate.get('feature_config', {}).get('interval'),
            'family': 'research-xgboost-v1' if candidate.get('artifact_version') == 1 else None,
            'feature_fingerprint': digest({key: candidate.get(key) for key in
                ('columns', 'feature_config', 'feature_set')}) if candidate else None,
            'contract_fingerprint': digest({key: value for key, value in candidate.items() if key != 'path'}) if candidate else None}


def decision_identity(*, symbol, model_id, origin, horizon, payload, model=None,
                      target_definition=None, frequency=None, security_id=None, strict=False):
    contract = model_contract(model)
    inferred_task = 'binary' if 'probability' in payload else 'regression' if 'predicted_return' in payload else None
    if strict and (not model or not model.get('artifact_sha256') or not contract['task']
                   or not contract['horizon'] or contract['threshold'] is None or not contract['frequency']):
        raise ValueError('Historical model/version/target contract cannot be proven from persisted metadata')
    if strict:
        metadata = model.get('metadata', {})
        if model.get('task') != contract['task']:
            raise ValueError('Registry task conflicts with persisted prediction contract')
        if metadata.get('legacy'):
            binding = metadata.get('binding', {})
            if (digest(metadata['contract']) != binding.get('fingerprint')
                    or binding.get('model_sha256') != model['artifact_sha256']
                    or metadata['contract'].get('ticker') != symbol
                    or model_id != 'legacy-' + binding['fingerprint'][:32]):
                raise ValueError('Persisted legacy version proof conflicts')
        elif (model_id != model['artifact_sha256']
              or metadata.get('candidate', {}).get('artifact_sha256') != model['artifact_sha256']
              or metadata.get('candidate', {}).get('ticker') != symbol):
            raise ValueError('Persisted candidate version proof conflicts')
    if model and model.get('symbol') != symbol:
        raise ValueError('Forecast security conflicts with model registry')
    task = contract['task'] or (model or {}).get('task') or inferred_task or 'unspecified'
    if inferred_task and task != inferred_task:
        raise ValueError('Prediction task conflicts with model contract')
    if type(horizon) is not int or horizon < 1:
        raise ValueError('Horizon must be a positive session count')
    if payload.get('horizon', horizon) != horizon:
        raise ValueError('Payload horizon conflicts with issuance horizon')
    if contract['horizon'] is not None and contract['horizon'] != horizon:
        raise ValueError('Horizon conflicts with immutable model contract')
    threshold = contract['threshold'] if contract['threshold'] is not None else .002
    target = target_definition or {'task': task, 'measure': 'adjusted_close_log_return',
                                  'operator': '>' if task == 'binary' else None,
                                  'event_threshold': float(threshold)}
    if target.get('task') != task:
        raise ValueError('Target task conflicts with model output')
    if not math.isfinite(float(target.get('event_threshold', threshold))):
        raise ValueError('Target threshold must be finite')
    if contract['threshold'] is not None and target.get('event_threshold') != float(contract['threshold']):
        raise ValueError('Target conflicts with immutable model contract')
    frequency = frequency or contract['frequency'] or '1d'
    if frequency != '1d':
        raise ValueError('Only daily session forecasts are supported')
    day = pd.Timestamp(origin)
    if pd.isna(day):
        raise ValueError('Origin cannot be missing')
    # Source origin is an exchange-session label, not a UTC timestamp to date-shift.
    return {'version': 1, 'model_id': model_id,
            'model_artifact_sha256': (model or {}).get('artifact_sha256'),
            'model_contract_fingerprint': contract['contract_fingerprint'],
            'model_family': contract['family'],
            'security': {'namespace': 'permanent' if security_id else 'symbol',
                         'id': security_id or symbol, 'symbol': symbol, 'exchange': 'XNYS'},
            'origin': day.date().isoformat(), 'horizon': horizon,
            'frequency': frequency, 'target_definition': target}


def prediction_content(payload):
    # Observation-time health and redundant source timestamps do not change model output.
    return {key: value for key, value in payload.items()
            if key not in {'data_freshness', 'generated_at', 'origin_time', 'target_time',
                           'target_time_kind', 'snapshots', 'input_sha256'}}
