"""Explicit local bindings; no discovery by test score and no model fitting."""
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path

from ..config import FeatureConfig, PredictionConfig
from ..models.registry import canonical, fingerprint, load_model
from .schemas import PredictionError

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _load(path: str, contract_json: str, weight_hash: str):
    logger.info('Loading bound XGBoost artifact %s', path)
    return load_model(Path(path), json.loads(contract_json))


def load_bound_model(ticker: str, model: str, manifest: Path | None = None, *, task: str = "binary"):
    config = PredictionConfig()
    if task not in {"binary", "regression"} or model != (config.model if task == "binary" else "xgboost_regressor"):
        raise PredictionError('UNSUPPORTED_MODEL', 'Application inference currently supports XGBoost; GRU remains a research challenger.', 400)
    manifest = Path(manifest or os.environ.get('STOCK_MODEL_MANIFEST', ROOT/'config/prediction_models.json'))
    try:
        binding = json.loads(manifest.read_text())['bindings'].get(f'{ticker}:{model}')
        if binding is None:
            raise FileNotFoundError(ticker)
        path = (ROOT / binding['artifact']).resolve()
        metadata = json.loads((path/'metadata.json').read_text())
        contract = metadata['contract']
        expected = {'task': task, 'horizon': config.horizon, 'threshold': config.event_threshold}
        if (fingerprint(contract) != binding['fingerprint'] or metadata['fingerprint'] != binding['fingerprint']
                or metadata['model_sha256'] != binding['model_sha256']
                or contract['ticker'] != ticker or contract['model_type'] != model
                or contract['target'] != expected or contract['interval'] != config.interval
                or contract['feature_config'] != canonical(asdict(FeatureConfig()))
                or contract['feature_version'] != 'features-v1'
                or metadata['model_file'] != 'model.json'
                or json.loads((path/'feature_names.json').read_text()) != contract['feature_names']
                or hashlib.sha256((path/'model.json').read_bytes()).hexdigest() != binding['model_sha256']):
            raise ValueError('Artifact identity, feature, or target mismatch')
        loaded = _load(str(path), json.dumps(contract, sort_keys=True), binding['model_sha256'])
        return loaded, contract, binding['fingerprint']
    except FileNotFoundError as exc:
        raise PredictionError('MODEL_NOT_AVAILABLE', f'No bound, compatible saved XGBoost artifact for {ticker}. Train offline and bind an artifact.') from exc
    except (ValueError, KeyError, OSError, TypeError) as exc:
        logger.warning('Rejected prediction artifact for %s: %s', ticker, exc)
        raise PredictionError('MODEL_INCOMPATIBLE', 'The saved artifact failed identity, checksum, or configuration validation.') from exc
