"""Bounded local dashboard training; research artifacts are never overwritten."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
from time import monotonic
from uuid import uuid4

import pandas as pd

from ..config import DEFAULT_SPLIT, PredictionConfig, TargetConfig
from ..models import XGBoostClassifier
from ..models.return_model import XGBoostReturnRegressor
from ..models.registry import model_contract, save_model
from ..training.feature_dataset import build_feature_dataset
from ..training.model_inputs import fit_partitioned
from .artifact_loader import ROOT, load_bound_model
from .schemas import PredictionError
from .service import load_history, _trim_invalid_trailing_rows

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='stock-training')
_lock = Lock()
_jobs: dict = {}
_finished_at: dict[str, float] = {}
FAILED_RETRY_SECONDS = 60
logger = logging.getLogger(__name__)


def _purge_finished_jobs(now: float) -> None:
    """Remove expired non-running jobs while holding ``_lock``."""
    for expired in tuple(_jobs):
        job = _jobs[expired]
        finished_at = _finished_at.get(expired, 0)
        if job["status"] != "training" and now - finished_at >= FAILED_RETRY_SECONDS:
            _jobs.pop(expired)
            _finished_at.pop(expired, None)


def train_and_bind(ticker: str, task: str = "binary") -> None:
    cfg = PredictionConfig()
    history = load_history(ticker)
    clock = pd.Timestamp.now(tz=getattr(history.index, 'tz', None) or 'UTC')
    history = _trim_invalid_trailing_rows(
        history.loc[pd.DatetimeIndex(history.index).date < clock.date()])
    dataset = build_feature_dataset(history, target_config=TargetConfig(
        task=task, horizon=cfg.horizon, threshold=cfg.event_threshold))
    model = XGBoostClassifier() if task == "binary" else XGBoostReturnRegressor()
    logger.info('Training dashboard XGBoost for %s on %d completed candles', ticker, len(history))
    fit_partitioned(model, dataset)
    contract = model_contract(model, dataset, ticker, asdict(DEFAULT_SPLIT))
    path = save_model(model, ROOT/'artifacts'/'dashboard'/ticker/uuid4().hex,
                      contract, {}, {'purpose': 'automatic dashboard training',
                                     'created_at': pd.Timestamp.now(tz='UTC').isoformat()})
    metadata = json.loads((path/'metadata.json').read_text())
    manifest = Path(os.environ.get('STOCK_MODEL_MANIFEST', ROOT/'config/prediction_models.json'))
    saved = json.loads(manifest.read_text()) if manifest.exists() else {'version': 1, 'bindings': {}}
    saved['bindings'][f'{ticker}:{model.model_type}'] = {
        'artifact': str(path), 'fingerprint': metadata['fingerprint'],
        'model_sha256': metadata['model_sha256']}
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_name(f'.{manifest.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(saved, indent=2))
        load_bound_model(ticker, model.model_type, temporary, task=task)
        temporary.replace(manifest)
        logger.info('Saved and bound dashboard XGBoost for %s at %s', ticker, path)
    finally:
        temporary.unlink(missing_ok=True)


def _run(ticker: str, task: str = "binary") -> None:
    key = ticker if task == "binary" else f"{ticker}:{task}"
    try:
        if task == "binary":
            train_and_bind(ticker)
        else:
            train_and_bind(ticker, task)
        result = {'status': 'ready', 'ticker': ticker}
    except Exception as exc:
        logger.exception('Dashboard training failed for %s', ticker)
        result = {'status': 'failed', 'ticker': ticker, 'error': str(exc)}
    with _lock:
        _jobs[key] = result
        _finished_at[key] = monotonic()


def request_training(ticker: str, task: str = "binary") -> dict:
    if task not in {"binary", "regression"}:
        raise PredictionError("INVALID_REQUEST", "Unsupported training task", 400)
    ticker = ticker.upper()
    if not re.fullmatch(r'[A-Z0-9.^=_-]{1,32}', ticker) or ticker in {'.', '..'}:
        raise PredictionError('INVALID_REQUEST', 'Invalid ticker', 400)
    try:
        if task == 'binary':
            load_bound_model(ticker, 'xgboost')
        else:
            load_bound_model(ticker, 'xgboost_regressor', task=task)
        return {'status': 'ready', 'ticker': ticker}
    except PredictionError as exc:
        if exc.code != 'MODEL_NOT_AVAILABLE':
            raise
    key = ticker if task == "binary" else f"{ticker}:{task}"
    with _lock:
        # Retain failures briefly so browser polling cannot create retry storms.
        _purge_finished_jobs(monotonic())
        if _jobs.get(key, {}).get('status') == 'ready':
            _jobs.pop(key)  # A previously bound artifact was removed.
            _finished_at.pop(key, None)
        if key in _jobs:
            return dict(_jobs[key])
        if sum(j['status'] == 'training' for j in _jobs.values()) >= 4:
            raise PredictionError('TRAINING_BUSY', 'Training queue is full. Try again shortly.', 429)
        _jobs[key] = {'status': 'training', 'ticker': ticker}
        _executor.submit(_run, ticker, task)
        return dict(_jobs[key])
