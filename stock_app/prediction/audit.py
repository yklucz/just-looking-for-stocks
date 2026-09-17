"""Evaluate a bound frozen model on an explicitly supplied history snapshot.

Example: python -m stock_app.prediction.audit --ticker AAPL --history path/to/market_history.csv
Does not train, bind, download data, or modify any model artifacts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from .artifact_loader import load_bound_model
from .evaluation import evaluate_predictions
from .schemas import PredictionError
from .service import _restrict_to_artifact_history, _validate_features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--history', type=Path, required=True)
    args = parser.parse_args()
    raw = pd.read_csv(args.history)
    raw.index = pd.to_datetime(raw.pop('timestamp'), utc=True)
    raw = raw.drop(columns=['raw_position'], errors='ignore')
    result = {'ticker': args.ticker.upper(), 'history_path': str(args.history.resolve()),
              'history_sha256': hashlib.sha256(args.history.read_bytes()).hexdigest(),
              'history_start': raw.index[0].isoformat(), 'history_end': raw.index[-1].isoformat(),
              'method': 'Frozen bound model; all resolved post-validation origins in supplied snapshot. No training or model selection. Historical replay, not contemporaneously logged predictions.',
              'models': {}}
    for task, model_type in [('binary', 'xgboost'), ('regression', 'xgboost_regressor')]:
        try:
            model, contract, version = load_bound_model(args.ticker.upper(), model_type, task=task)
        except PredictionError as exc:
            result['models'][model_type] = {'status': 'unavailable', 'reason': str(exc)}
            continue
        history = _restrict_to_artifact_history(raw, contract)
        features = _validate_features(history, contract)
        available = pd.to_datetime(contract['fit_dates']['validation']['label_end'], utc=True)
        rows = features.frame.loc[pd.to_datetime(features.frame.index, utc=True) > available]
        outputs = model.predict_proba(rows) if task == 'binary' else model.predict(rows)
        evaluation = evaluate_predictions(history.Close, pd.Series(outputs,index=rows.index),
            horizon=contract['target']['horizon'], threshold=contract['target']['threshold'],
            available_after=available, task=task)
        result['models'][model_type] = {'version': version, 'fit_dates': contract['fit_dates'],
                                       'evaluation': evaluation}
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
