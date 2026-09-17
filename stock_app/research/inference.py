"""Adapt verified research candidates to existing daily prediction responses."""
from pathlib import Path
import json
import hashlib

import numpy as np
import pandas as pd

from .lifecycle import verify_artifact
from .store import utcnow


def record_legacy_inference(ticker, version, history, response, *, payload=None):
    """Pin the exact adjusted inputs used by legacy inference, without inventing raw prices."""
    from .runtime import get_runtime
    from .ledger import issue_forecast
    runtime = get_runtime()
    model_id = 'legacy-' + version[:32]
    runtime.store.get('models', model_id)
    content = history.to_csv(index_label='timestamp').encode()
    checksum = hashlib.sha256(content).hexdigest()
    identity = f'legacy-input-{ticker}-{checksum}'
    path = runtime.root / 'issued-inputs' / (identity + '.csv')
    path.parent.mkdir(exist_ok=True)
    try:
        with path.open('xb') as handle:
            handle.write(content)
    except FileExistsError:
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError('Issued input snapshot checksum mismatch')
    try:
        runtime.store.get('datasets', identity)
    except KeyError:
        runtime.store.put('datasets', {'id': identity, 'symbol': ticker, 'source': 'legacy inference input',
                                       'status': 'valid', 'path': str(path), 'sha256': checksum,
                                       'rows': len(history), 'start': str(history.index[0]), 'end': str(history.index[-1]),
                                       'downloaded_at': response['generated_at'],
                                       'adjustment': 'Already adjusted legacy OHLCV; raw prices unavailable in this snapshot'})
    if payload is None:
        payload = {'probability': response['prediction']['probability_up'],
                   'training_prior': None, 'calibrated': False}
    return issue_forecast(runtime.store, symbol=ticker, model_id=model_id, snapshot_id=identity,
                          origin=response['timestamp'], issued_at=response['generated_at'],
                          payload={**payload, 'origin_close': float(history.Close.iloc[-1]), 'input_sha256': checksum})


def model_payload(model, history, contexts=None):
    verify_artifact(model)
    if not model.get('metadata', {}).get('legacy'):
        from .experiments import predict_candidate
        result = predict_candidate(model['artifact'], history, contexts)
        result['origin_close'] = float(history.Close.iloc[-1])
        return result
    from ..models.registry import load_model
    from ..features import build_features
    contract = json.loads((Path(model['artifact']) / 'metadata.json').read_text())['contract']
    # Legacy artifacts preserve their original start date; normalize only the session identity.
    start = pd.Timestamp(contract['history_start']).date()
    history = history.loc[history.index.date >= start, ['Open', 'High', 'Low', 'Close', 'Volume']]
    if history.empty or history.index[0].date() != start:
        raise ValueError('Legacy model requires its original historical context')
    cutoff = pd.Timestamp(contract['fit_dates']['validation']['label_end']).date()
    if history.index[-1].date() <= cutoff:
        raise ValueError('Prediction origin must follow validation outcomes')
    features = build_features(history)
    if list(features.feature_names) != contract['feature_names'] or not features.valid_mask.iloc[-1]:
        raise ValueError('Invalid or mismatched legacy features')
    estimator = load_model(Path(model['artifact']), contract)
    result = {'origin_time': history.index[-1].isoformat(), 'origin_close': float(history.Close.iloc[-1]),
              'horizon': 5, 'training_prior': None, 'calibrated': False,
              'trained_until': contract['fit_dates']['validation']['label_end'],
              'base_training_label_end': contract['fit_dates']['train']['label_end']}
    if model['task'] == 'binary':
        result['probability'] = float(estimator.predict_proba(features.all_features.iloc[[-1]])[0])
    else:
        result['predicted_return'] = float(estimator.predict(features.all_features.iloc[[-1]])[0])
    return result


def active_prediction(ticker, task='binary'):
    from .runtime import get_runtime
    runtime = get_runtime()
    model = runtime.store.active(ticker, task)
    if model is None or model.get('metadata', {}).get('legacy'):
        return None
    history, contexts, mapping = runtime.inputs(ticker)
    payload = model_payload(model, history, contexts)
    payload['data_freshness'] = history.attrs.get('data_freshness', {})
    from .ledger import issue_forecast
    snapshot = hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()
    issued = issue_forecast(runtime.store, symbol=ticker, model_id=model['id'], snapshot_id=snapshot,
                            origin=payload['origin_time'], payload={**payload, 'snapshots': mapping})
    payload['forecast_id'] = issued['id']
    payload['evaluation_kind'] = issued['kind']
    return model, history, payload


def prediction_response(ticker):
    active = active_prediction(ticker)
    if active is None:
        return None
    model, history, payload = active
    probability = float(payload['probability'])
    if not np.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError('Invalid model probability')
    trained = payload.get('trained_until') or payload.get('available_after') or model['created_at']
    age_days = max(0, (pd.Timestamp(utcnow()) - pd.to_datetime(trained, utc=True)).days)
    from .calendar import latest_completed_session
    inputs = payload.get('data_freshness', {})
    stale = (history.index[-1].date() < latest_completed_session().date()
             or any(item['stale'] for item in inputs.values()))
    return {'schema_version': 'prediction-v2', 'ticker': ticker, 'symbol': ticker,
            'timestamp': payload['origin_time'], 'generated_at': utcnow(), 'interval': '1d',
            'prediction': {'task': 'binary_return_event', 'horizon': 5, 'event_threshold': .002,
                           'probability_up': probability, 'target_definition': 'log(Close[t+5] / Close[t]) > 0.002',
                           'calibrated': payload.get('calibrated', True)},
            'signal': {'action': 'LONG' if probability >= .5 else 'FLAT', 'decision_threshold': .5, 'mode': 'research'},
            'model': {'type': 'xgboost', 'version': model['id'], 'trained_until': trained,
                      'age_days': age_days, 'stale': age_days > 365},
            'market': {'stale': stale, 'last_completed_candle': payload['origin_time'], 'inputs': inputs},
            'forecast_id': payload['forecast_id'], 'evaluation_kind': payload['evaluation_kind'],
            'note': 'Historical performance does not establish economic value.'}


def chart_response(ticker, legacy_response=None):
    errors = {}
    def available(task):
        try:
            return active_prediction(ticker, task)
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            errors[task] = str(exc)
            return None
    classification, regression = available('binary'), available('regression')
    if classification is None and regression is None and not errors:
        return legacy_response
    result = dict(legacy_response or {'ticker': ticker, 'probabilities': [], 'forecast': None,
                                      'forecast_status': 'missing', 'evaluation': {}})
    result['evaluation'] = dict(result.get('evaluation', {}))
    if 'binary' in errors:
        result.update(probabilities=[], probability_status='unavailable', probability_error=errors['binary'])
    if 'regression' in errors:
        result.update(forecast=None, forecast_status='unavailable', forecast_error=errors['regression'])
    if classification:
        model, _, payload = classification
        result.update(probability_version=model['id'], probabilities=[{
            'timestamp': payload['origin_time'], 'probability': payload['probability']}])
        result['evaluation']['classification'] = None
    if regression:
        model, _, payload = regression
        origin_close = payload['origin_close']
        forecast = {'origin': payload['origin_time'], 'origin_close': origin_close, 'horizon': 5,
                    'predicted_log_return': payload['predicted_return'],
                    'estimated_price': float(origin_close * np.exp(payload['predicted_return'])),
                    'model_version': model['id'], 'label': 'Estimated daily Close',
                    'note': 'One five-session estimate; nominal ranges do not guarantee coverage.'}
        if payload.get('lower_return') is not None and payload.get('upper_return') is not None:
            forecast.update(lower_price=float(origin_close * np.exp(payload['lower_return'])),
                            upper_price=float(origin_close * np.exp(payload['upper_return'])), nominal_coverage=.8)
        result.update(forecast=forecast, forecast_status='ready')
        result['evaluation']['regression'] = None
    kinds = {'classification': classification[2]['evaluation_kind'] if classification else 'historical_replay',
             'regression': regression[2]['evaluation_kind'] if regression else 'historical_replay'}
    result.update(generated_at=utcnow(), evaluation_kinds=kinds,
                  forecast_ids={'classification': classification[2].get('forecast_id') if classification else None,
                                'regression': regression[2].get('forecast_id') if regression else None},
                  evaluation_kind=next(iter(kinds.values())) if len(set(kinds.values())) == 1 else 'mixed',
                  note='Current qualified model. See Research for issued forecasts and resolved outcomes.')
    return result
