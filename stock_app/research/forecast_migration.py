"""Explicit, additive migration. Original records stay byte-for-byte in records."""
from collections import defaultdict
import json

import pandas as pd

from .forecast_identity import decision_identity, digest, prediction_content, model_contract
from .forecast_store import append
from .ledger import session_times


def _validated(row, model):
    for key in ('snapshot_id', 'issued_at', 'created_at', 'horizon', 'target', 'payload'):
        if key not in row:
            raise ValueError(f'Missing historical {key}')
    issued, recorded = pd.Timestamp(row['issued_at']), pd.Timestamp(row['created_at'])
    if any(pd.isna(t) or t.tzinfo is None for t in (issued, recorded)):
        raise ValueError('Historical timestamps must be timezone-aware')
    close, next_open, target, _ = session_times(row['origin'], row['horizon'])
    if row['target'] != target.date().isoformat():
        raise ValueError('Historical target session conflicts with horizon')
    if row.get('kind') not in {'prospective', 'historical_replay'}:
        raise ValueError('Unknown historical issuance kind')
    if row['kind'] == 'prospective' and not (close <= issued < next_open and recorded < next_open):
        raise ValueError('Prospective timing cannot be proven')
    if 'origin_close' not in row['payload'] or float(row['payload']['origin_close']) <= 0:
        raise ValueError('Missing historical prediction price basis')
    return decision_identity(symbol=row['symbol'], model_id=row['model_id'], origin=row['origin'],
                             horizon=row['horizon'], payload=row['payload'], model=model, strict=True)


def migrate_forecasts(store):
    """One atomic migration transaction; group ambiguity never selects a later winner."""
    stats = {'historical_rows_examined': 0, 'canonical_issuances_produced': 0,
             'duplicate_logical_groups_collapsed': 0, 'revision_records_produced': 0,
             'ambiguous_groups': 0, 'ambiguous_rows': 0, 'unchanged_records': 0}
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        models = {row['id']: row for row in (json.loads(r[0]) for r in db.execute(
            "SELECT document FROM records WHERE kind='models'"))}
        rows = [json.loads(r[0]) for r in db.execute("SELECT document FROM records WHERE kind='forecasts'")]
        existing = {r['legacy_id']: dict(r) for r in db.execute('SELECT * FROM forecast_migration')}
        groups = defaultdict(list)
        for row in rows:
            stats['historical_rows_examined'] += 1
            if row['id'] in existing:
                if existing[row['id']]['original_hash'] != digest(row):
                    raise ValueError('Migrated legacy evidence changed; reconcile before continuing')
                stats['unchanged_records'] += 1
                continue
            # An uncertain earliest row contaminates this whole possible decision group.
            base = tuple(str(row.get(key)) for key in ('symbol', 'model_id', 'origin'))
            groups[base].append(row)
        for group in groups.values():
            reason = None
            identities = {}
            try:
                for row in group:
                    identities[row['id']] = _validated(row, models.get(row.get('model_id')))
                if len({digest(i) for i in identities.values()}) != 1:
                    raise ValueError('Historical decision contracts disagree')
                group.sort(key=lambda row: (pd.Timestamp(row['created_at']), row['id']))
                first_time = pd.Timestamp(group[0]['created_at'])
                first_outputs = {digest(prediction_content(r['payload'])) for r in group
                                 if pd.Timestamp(r['created_at']) == first_time}
                if len(first_outputs) > 1:
                    raise ValueError('Earliest authority ordering is ambiguous')
                identity = identities[group[0]['id']]
                issuance_id = 'fc-' + digest(identity)
                seen = {}
                for saved in db.execute('SELECT document,output_hash FROM forecast_revisions WHERE issuance_id=?', (issuance_id,)):
                    revision = json.loads(saved[0])
                    key = digest([revision['snapshot_id'], revision['payload'].get('snapshots'), revision['payload'].get('input_sha256')])
                    seen[key] = saved[1]
                for row in group:
                    key = digest([row['snapshot_id'], row['payload'].get('snapshots'), row['payload'].get('input_sha256')])
                    output = digest(prediction_content(row['payload']))
                    if key in seen and seen[key] != output:
                        raise ValueError('Equivalent historical inputs contain conflicting predictions')
                    seen[key] = output
                existing_issuance = db.execute('SELECT document FROM forecast_issuances WHERE id=?', (issuance_id,)).fetchone()
                if existing_issuance:
                    current = json.loads(existing_issuance[0])
                    if pd.Timestamp(group[0]['created_at']) < pd.Timestamp(current['created_at']):
                        raise ValueError('Earlier legacy authority conflicts with an existing canonical issuance')
                    if pd.Timestamp(group[0]['created_at']) == pd.Timestamp(current['created_at']):
                        authority = json.loads(db.execute('SELECT document FROM forecast_revisions WHERE id=?',
                                                          (current['authoritative_revision_id'],)).fetchone()[0])
                        if digest(prediction_content(authority['payload'])) not in first_outputs:
                            raise ValueError('Historical authority tie conflicts with an existing canonical issuance')
            except (ValueError, KeyError, TypeError, OverflowError) as error:
                reason = str(error)
            if reason:
                stats['ambiguous_groups'] += 1
                stats['ambiguous_rows'] += len(group)
                for row in group:
                    db.execute('INSERT INTO forecast_migration VALUES(?,?,?,?,?,?)',
                               (row['id'], 'ambiguous', None, None, reason, digest(row)))
                continue
            before_revisions = db.execute('SELECT count(*) FROM forecast_revisions WHERE issuance_id=?', (issuance_id,)).fetchone()[0]
            for row in group:
                result = append(db, identities[row['id']], row,
                                provenance={'legacy_id': row['id'], 'legacy_record': row,
                                            'feature_fingerprint': model_contract(models[row['model_id']])['feature_fingerprint'],
                                            'model_artifact_sha256': identity['model_artifact_sha256']})
                db.execute('INSERT INTO forecast_migration VALUES(?,?,?,?,?,?)',
                           (row['id'], 'mapped', result['id'], result['revision_id'], None, digest(row)))
            after_revisions = db.execute('SELECT count(*) FROM forecast_revisions WHERE issuance_id=?', (issuance_id,)).fetchone()[0]
            stats['revision_records_produced'] += after_revisions - before_revisions
            stats['canonical_issuances_produced'] += int(not existing_issuance)
            stats['duplicate_logical_groups_collapsed'] += int(len(group) > 1)
    return stats
