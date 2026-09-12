"""Chart estimates from frozen models; historical points follow validation labels."""
import numpy as np
import pandas as pd
from ..config import PredictionConfig
from .artifact_loader import load_bound_model
from .schemas import PredictionError
from .service import (_completed_history, _prediction_clock, _restrict_to_artifact_history,
                      _validate_features, _validate_prediction_origin, _validate_ticker)


def prediction_chart(ticker: str) -> dict:
    ticker = _validate_ticker(ticker)
    classifier, contract, version = load_bound_model(ticker, 'xgboost')
    history, _ = _completed_history(ticker, _prediction_clock(None))
    context = _restrict_to_artifact_history(history, contract)
    features = _validate_features(context, contract)
    available = pd.to_datetime(contract['fit_dates']['validation']['label_end'], utc=True)
    rows = features.frame.loc[pd.to_datetime(features.frame.index, utc=True) > available].tail(2000)
    if rows.empty:
        raise ValueError('No prediction origins after model validation outcomes')
    probabilities = classifier.predict_proba(rows)
    if not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError('Invalid model probabilities')
    result = {'ticker': ticker, 'generated_at': pd.Timestamp.now(tz='UTC').isoformat(),
              'probability_version': version,
              'probabilities': [{'timestamp': t.isoformat(), 'probability': float(p)}
                                for t, p in zip(rows.index, probabilities)],
              'forecast': None, 'forecast_status': 'missing',
              'note': 'Daily completed-candle estimates. Historical probabilities exclude model fitting and validation periods.'}
    try:
        regressor, rc, rv = load_bound_model(ticker, 'xgboost_regressor', task='regression')
        rh = _restrict_to_artifact_history(history, rc)
        rf = _validate_features(rh, rc)
        origin = _validate_prediction_origin(rh, rc)
        expected_log_return = float(regressor.predict(rf.frame.iloc[[-1]])[0])
        with np.errstate(over='ignore'):
            estimated_price = float(rh.Close.iloc[-1] * np.exp(expected_log_return))
        if not np.isfinite(expected_log_return) or not np.isfinite(estimated_price) or estimated_price <= 0:
            raise ValueError('Invalid return regression output')
        result['forecast'] = {'origin': origin.isoformat(), 'origin_close': float(rh.Close.iloc[-1]),
                              'horizon': PredictionConfig().horizon,
                              'predicted_log_return': expected_log_return,
                              'estimated_price': estimated_price, 'model_version': rv,
                              'label': 'Estimated price from predicted log return',
                              'note': 'Dashed connector joins the observed close to one horizon estimate; intermediate prices are not predicted. This regressor has not established out-of-sample economic value.'}
        result['forecast_status'] = 'ready'
    except PredictionError as exc:
        result['forecast_status'] = 'missing' if exc.code == 'MODEL_NOT_AVAILABLE' else 'unavailable'
        result['forecast_error'] = str(exc)
    return result
