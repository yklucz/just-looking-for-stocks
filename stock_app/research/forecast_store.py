"""Transactional canonical issuances and append-only input revisions."""
import json

from .forecast_identity import canonical_json, digest, prediction_content


def create_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS forecast_issuances (
            id TEXT PRIMARY KEY, identity_json TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL, model_id TEXT NOT NULL, origin TEXT NOT NULL,
            authoritative_revision_id TEXT NOT NULL, document TEXT NOT NULL,
            UNIQUE(id, authoritative_revision_id),
            FOREIGN KEY(id, authoritative_revision_id) REFERENCES forecast_revisions(issuance_id,id)
                DEFERRABLE INITIALLY DEFERRED);
        CREATE TABLE IF NOT EXISTS forecast_revisions (
            id TEXT PRIMARY KEY, issuance_id TEXT NOT NULL, input_key TEXT NOT NULL,
            output_hash TEXT NOT NULL, document TEXT NOT NULL,
            UNIQUE(issuance_id,input_key), UNIQUE(issuance_id,id),
            FOREIGN KEY(issuance_id) REFERENCES forecast_issuances(id) DEFERRABLE INITIALLY DEFERRED);
        CREATE INDEX IF NOT EXISTS forecast_issuances_query ON forecast_issuances(symbol,model_id,origin);
        CREATE INDEX IF NOT EXISTS forecast_issuances_model ON forecast_issuances(model_id,origin);
        CREATE TABLE IF NOT EXISTS forecast_migration (
            legacy_id TEXT PRIMARY KEY, status TEXT NOT NULL CHECK(status IN ('mapped','ambiguous')),
            issuance_id TEXT, revision_id TEXT, reason TEXT, original_hash TEXT NOT NULL,
            FOREIGN KEY(issuance_id,revision_id) REFERENCES forecast_revisions(issuance_id,id));
        CREATE TRIGGER IF NOT EXISTS forecast_revision_immutable_update
            BEFORE UPDATE ON forecast_revisions BEGIN SELECT RAISE(ABORT,'Forecast revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS forecast_revision_immutable_delete
            BEFORE DELETE ON forecast_revisions BEGIN SELECT RAISE(ABORT,'Forecast revisions are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS forecast_issuance_identity_immutable
            BEFORE UPDATE OF id,identity_json,symbol,model_id,origin,authoritative_revision_id ON forecast_issuances
            BEGIN SELECT RAISE(ABORT,'Forecast identity and authoritative prediction are immutable'); END;
    ''')


def project(issuance, revision, *, requested=False):
    """Keep the dashboard's flattened shape while returning a stable issuance ID."""
    result = {**issuance, 'snapshot_id': revision['snapshot_id'], 'payload': revision['payload'],
              'revision_id': revision['id'], 'authoritative_revision_id': issuance['authoritative_revision_id']}
    if requested:
        result.update(kind=revision['kind'], issued_at=revision['issued_at'],
                      canonical_kind=issuance['kind'],
                      revision_of=issuance['id'] if revision.get('previous_revision_id') else None)
    return result


def get(db, identity):
    row = db.execute('''SELECT i.document,r.document FROM forecast_issuances i
                        JOIN forecast_revisions r ON r.id=i.authoritative_revision_id AND r.issuance_id=i.id
                        WHERE i.id=?''', (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    return project(json.loads(row[0]), json.loads(row[1]))


def list_issuances(db, *, symbol=None, model_id=None, state=None):
    query = '''SELECT i.document,r.document FROM forecast_issuances i
               JOIN forecast_revisions r ON r.issuance_id=i.id AND r.id=i.authoritative_revision_id WHERE 1=1'''
    args = []
    for column, value in [('symbol', symbol), ('model_id', model_id)]:
        if value:
            query += f' AND i.{column}=?'
            args.append(value)
    rows = [project(json.loads(row[0]), json.loads(row[1])) for row in db.execute(query + ' ORDER BY i.origin DESC,i.id', args)]
    return [row for row in rows if state is None or row['state'] == state]


def revisions(db, issuance_id):
    return [json.loads(row[0]) for row in db.execute(
        'SELECT document FROM forecast_revisions WHERE issuance_id=? ORDER BY rowid', (issuance_id,))]


def update_evaluation(db, identity, changes):
    if not set(changes).issubset({'state', 'outcome', 'outcome_revisions'}):
        raise ValueError('Canonical identity and prediction are immutable; append an input revision instead')
    row = db.execute('SELECT document FROM forecast_issuances WHERE id=?', (identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    value = json.loads(row[0])
    value.update(changes)
    db.execute('UPDATE forecast_issuances SET document=? WHERE id=?', (canonical_json(value), identity))
    return get(db, identity)


def append(db, identity, record, *, provenance=None):
    """Caller owns one IMMEDIATE transaction. All three writes commit or roll back."""
    issuance_id = 'fc-' + digest(identity)
    input_key = digest({'snapshot_id': record['snapshot_id'], 'snapshots': record['payload'].get('snapshots'),
                        'input_sha256': record['payload'].get('input_sha256')})
    revision_id = 'fr-' + digest([issuance_id, input_key])
    output_hash = digest(prediction_content(record['payload']))
    existing = db.execute('SELECT document,output_hash FROM forecast_revisions WHERE issuance_id=? AND input_key=?',
                          (issuance_id, input_key)).fetchone()
    if existing:
        if existing[1] != output_hash:
            raise ValueError('Same model/input produced different outputs; reconciliation required')
        return project(json.loads(db.execute('SELECT document FROM forecast_issuances WHERE id=?',
                                            (issuance_id,)).fetchone()[0]), json.loads(existing[0]), requested=True)
    row = db.execute('SELECT document FROM forecast_issuances WHERE id=?', (issuance_id,)).fetchone()
    previous = db.execute('SELECT id FROM forecast_revisions WHERE issuance_id=? ORDER BY rowid DESC LIMIT 1',
                          (issuance_id,)).fetchone()
    revision = {'id': revision_id, 'issuance_id': issuance_id, 'input_key': input_key,
                'snapshot_id': record['snapshot_id'], 'payload': record['payload'],
                'issued_at': record['issued_at'], 'created_at': record['created_at'], 'kind': record['kind'],
                'output_hash': output_hash, 'previous_revision_id': previous[0] if previous else None,
                'reason': 'input_revision' if previous else 'initial', 'provenance': provenance or {}}
    if row:
        issuance = json.loads(row[0])
    else:
        issuance = {key: record[key] for key in
                    ('symbol', 'model_id', 'origin', 'target', 'horizon', 'issued_at', 'created_at', 'kind', 'state')}
        issuance.update(id=issuance_id, identity=identity, authoritative_revision_id=revision_id, revision_of=None)
        for key in ('outcome', 'outcome_revisions'):
            if key in record:
                issuance[key] = record[key]
        db.execute('INSERT INTO forecast_issuances VALUES(?,?,?,?,?,?,?)',
                   (issuance_id, canonical_json(identity), record['symbol'], record['model_id'],
                    record['origin'], revision_id, canonical_json(issuance)))
    db.execute('INSERT INTO forecast_revisions VALUES(?,?,?,?,?)',
               (revision_id, issuance_id, input_key, output_hash, canonical_json(revision)))
    return project(issuance, revision, requested=True)
