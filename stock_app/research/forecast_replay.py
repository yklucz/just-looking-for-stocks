"""Offline verification of one retained input revision; never issues a forecast."""
import hashlib
from pathlib import Path

import pandas as pd

from .forecast_store import get, revisions


PREDICTION_FIELDS = ('probability', 'predicted_return', 'lower_return', 'upper_return', 'origin_close')


def replay_revision(runtime, issuance_id, revision_id):
    from .inference import model_payload
    with runtime.store.connection() as db:
        issuance = get(db, issuance_id)
        revision = next((row for row in revisions(db, issuance_id) if row['id'] == revision_id), None)
    if revision is None:
        raise KeyError(revision_id)
    model = runtime.store.get('models', issuance['model_id'])
    expected = issuance['identity']['model_artifact_sha256']
    if not expected or model.get('artifact_sha256') != expected:
        raise ValueError('Replay requires the recorded immutable model version')
    mapping = revision['payload'].get('snapshots')
    if mapping:
        frames = {symbol: runtime.data.load(identity) for symbol, identity in mapping.items()}
        history, contexts = frames[issuance['symbol']], frames
    else:
        dataset = runtime.store.get('datasets', revision['snapshot_id'])
        path = Path(dataset['path']).resolve()
        if not path.is_relative_to(runtime.root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != dataset['sha256']:
            raise ValueError('Replay input checksum/path mismatch')
        history = pd.read_csv(path, index_col=0, parse_dates=[0])
        history.index = pd.to_datetime(history.index, utc=True)
        contexts = None
    result = model_payload(model, history, contexts)
    if pd.Timestamp(result['origin_time']).date().isoformat() != issuance['origin']:
        raise ValueError('Replay origin mismatch')
    expected_values = {key: revision['payload'][key] for key in PREDICTION_FIELDS if key in revision['payload']}
    actual = {key: result.get(key) for key in expected_values}
    return {'issuance_id': issuance_id, 'revision_id': revision_id, 'matches': actual == expected_values,
            'recorded': expected_values, 'replayed': actual, 'mode': 'offline_verification'}
