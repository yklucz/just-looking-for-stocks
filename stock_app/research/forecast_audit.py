"""Read-only ledger invariants; no schema creation, migration, fitting or provider IO."""
import json
import sqlite3
from pathlib import Path

from .forecast_identity import digest, prediction_content


def audit_forecasts(path):
    path = Path(path).resolve()
    with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy_count = db.execute("SELECT count(*) FROM records WHERE kind='forecasts'").fetchone()[0]
        result = {'historical_rows': legacy_count, 'issuances': 0, 'revisions': 0,
                  'issuances_with_multiple_current_revisions': 0, 'orphan_revisions': 0,
                  'duplicate_canonical_identities': 0, 'duplicate_revision_identities': 0,
                  'missing_authoritative_revisions': 0, 'ambiguous_migrated_groups': 0,
                  'ambiguous_rows': 0, 'unmigrated_rows': legacy_count,
                  'evaluation_cardinality_mismatches': 0, 'integrity_errors': [], 'reconciliation': []}
        if 'forecast_issuances' not in tables:
            result['status'] = 'migration_required' if legacy_count else 'empty'
            return result
        issuances = list(db.execute('SELECT * FROM forecast_issuances'))
        revisions = list(db.execute('SELECT * FROM forecast_revisions'))
        by_id = {r['id']: r for r in revisions}
        issuance_ids = {i['id'] for i in issuances}
        result['issuances'], result['revisions'] = len(issuances), len(revisions)
        result['duplicate_canonical_identities'] = len(issuances) - len({i['identity_json'] for i in issuances})
        result['duplicate_revision_identities'] = len(revisions) - len({(r['issuance_id'], r['input_key']) for r in revisions})
        for issuance in issuances:
            document = json.loads(issuance['document'])
            identity = json.loads(issuance['identity_json'])
            revision = by_id.get(issuance['authoritative_revision_id'])
            if revision is None or revision['issuance_id'] != issuance['id']:
                result['missing_authoritative_revisions'] += 1
                if document.get('kind') == 'prospective':
                    result['evaluation_cardinality_mismatches'] += 1
            elif document.get('kind') == 'prospective':
                authoritative = json.loads(revision['document'])
                if (authoritative.get('kind') != 'prospective'
                        or authoritative.get('issued_at') != document.get('issued_at')):
                    result['evaluation_cardinality_mismatches'] += 1
            if (issuance['id'] != 'fc-' + digest(identity) or document.get('identity') != identity
                    or document.get('authoritative_revision_id') != issuance['authoritative_revision_id']):
                result['integrity_errors'].append(f"Issuance identity/pointer mismatch: {issuance['id']}")
        for revision in revisions:
            document = json.loads(revision['document'])
            result['orphan_revisions'] += int(revision['issuance_id'] not in issuance_ids)
            if (digest(prediction_content(document['payload'])) != revision['output_hash']
                    or document['output_hash'] != revision['output_hash']
                    or document.get('id') != revision['id'] or document.get('issuance_id') != revision['issuance_id']
                    or document.get('input_key') != revision['input_key']
                    or revision['input_key'] != digest({'snapshot_id': document['snapshot_id'],
                        'snapshots': document['payload'].get('snapshots'), 'input_sha256': document['payload'].get('input_sha256')})
                    or revision['id'] != 'fr-' + digest([revision['issuance_id'], revision['input_key']])):
                result['integrity_errors'].append(f"Revision fingerprint mismatch: {revision['id']}")
            previous = document.get('previous_revision_id')
            if previous and (previous not in by_id or by_id[previous]['issuance_id'] != revision['issuance_id']):
                result['integrity_errors'].append(f"Revision lineage mismatch: {revision['id']}")
        mappings = list(db.execute('SELECT * FROM forecast_migration'))
        result['unmigrated_rows'] -= len(mappings)
        ambiguous = [row for row in mappings if row['status'] == 'ambiguous']
        result['reconciliation'] = [{'legacy_id': row['legacy_id'], 'reason': row['reason']} for row in ambiguous]
        result['ambiguous_rows'] = len(ambiguous)
        legacy = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,document FROM records WHERE kind='forecasts'")}
        result['ambiguous_migrated_groups'] = len({tuple(str(legacy.get(r['legacy_id'], {}).get(key)) for key in
                                                   ('symbol', 'model_id', 'origin')) for r in ambiguous})
        for row in mappings:
            if row['legacy_id'] not in legacy or digest(legacy[row['legacy_id']]) != row['original_hash']:
                result['integrity_errors'].append(f"Original evidence changed: {row['legacy_id']}")
        result['integrity_errors'].extend(f'Foreign key violation: {tuple(row)}'
                                          for row in db.execute('PRAGMA foreign_key_check'))
        issues = ('orphan_revisions', 'duplicate_canonical_identities', 'duplicate_revision_identities',
                  'missing_authoritative_revisions', 'evaluation_cardinality_mismatches', 'integrity_errors')
        result['status'] = ('invalid' if any(result[key] for key in issues) else
                            'reconciliation_required' if result['ambiguous_rows'] or result['unmigrated_rows'] else 'healthy')
        return result
