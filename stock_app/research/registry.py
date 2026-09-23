"""Typed, append-only research control. SQL enforces immutable history and retries."""
import json
import math
from uuid import uuid4

from .forecast_identity import canonical_json, digest
from .store import utcnow

TABLES = ('questions', 'hypotheses', 'families', 'specs', 'trials', 'runs',
          'attempts', 'outcomes', 'events', 'artifacts', 'model_links')
TERMINAL = {'completed', 'failed', 'invalid', 'aborted', 'blocked'}


def meaningful(value, name):
    if not isinstance(value, str) or len(value.strip()) < 8:
        raise ValueError(f'{name} requires at least eight nonblank characters')
    return value.strip()


class Registry:
    def __init__(self, store, clock=utcnow):
        self.store, self.clock = store, clock

    def get(self, kind, identity, db=None):
        if kind not in TABLES or kind == 'model_links':
            raise ValueError('Unknown registry entity')
        if db is None:
            with self.store.connection() as connection:
                return self.get(kind, identity, connection)
        row = db.execute(f'SELECT * FROM research_{kind} WHERE id=?', (identity,)).fetchone()
        if row is None:
            raise KeyError(identity)
        value = dict(row)
        if 'document' in value:
            value['document'] = json.loads(value['document'])
        return value

    def list(self, kind='runs'):
        if kind not in TABLES:
            raise ValueError('Unknown registry entity')
        with self.store.connection() as db:
            rows = [dict(row) for row in db.execute(f'SELECT * FROM research_{kind} ORDER BY created_at,rowid')]
        for row in rows:
            if 'document' in row:
                row['document'] = json.loads(row['document'])
        return rows

    def event(self, kind, document, *, run_id=None, attempt_id=None, trial_id=None, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.event(kind, document, run_id=run_id, attempt_id=attempt_id,
                                  trial_id=trial_id, db=connection)
        identity = uuid4().hex
        db.execute('INSERT INTO research_events VALUES(?,?,?,?,?,?,?)',
                   (identity, self.clock(), run_id, attempt_id, trial_id, kind, canonical_json(document)))
        return identity

    def _register(self, kind, document, extra, *, fingerprint=None):
        fingerprint = fingerprint or digest(document)
        identity = kind[:3] + '-' + digest([fingerprint, extra.get('family_id') if kind == 'trials' else None])
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            columns = ['id', 'fingerprint', 'created_at', *extra, 'document']
            values = [identity, fingerprint, self.clock(), *extra.values(), canonical_json(document)]
            db.execute(f"INSERT OR IGNORE INTO research_{kind} ({','.join(columns)}) VALUES ({','.join('?' for _ in values)})", values)
            result = self.get(kind, identity, db)
            self.event('registration', {'entity': kind, 'id': identity, 'proposal': document}, db=db)
            return result

    def question(self, *, title, description, creator='local user', parent_id=None):
        if parent_id:
            self.get('questions', parent_id)
        return self._register('questions', {'title': meaningful(title, 'title'),
            'description': meaningful(description, 'description'), 'creator': creator,
            'status': 'registered', 'parent_fingerprint': self.get('questions', parent_id)['fingerprint'] if parent_id else None},
            {'parent_id': parent_id})

    def hypothesis(self, *, question_id, statement, effect, rationale, falsification):
        question = self.get('questions', question_id)
        doc = {name: meaningful(value, name) for name, value in
               [('statement', statement), ('effect', effect), ('rationale', rationale), ('falsification', falsification)]}
        doc.update(question_fingerprint=question['fingerprint'], status='registered')
        return self._register('hypotheses', doc, {'question_id': question_id})

    def family(self, *, hypothesis_id, dimensions):
        hypothesis = self.get('hypotheses', hypothesis_id)
        if not isinstance(dimensions, dict) or not dimensions or any(not isinstance(v, list) or not v for v in dimensions.values()):
            raise ValueError('Declare nonempty dimensions and allowed value lists')
        # Allowed values are sets; their proposal order is not research identity.
        dimensions = {k: sorted({canonical_json(x): x for x in v}.values(), key=canonical_json)
                      for k, v in dimensions.items()}
        return self._register('families', {'hypothesis_fingerprint': hypothesis['fingerprint'],
            'dimensions': dimensions}, {'hypothesis_id': hypothesis_id})

    def spec(self, *, hypothesis_id, family_id, contract):
        hypothesis, family = self.get('hypotheses', hypothesis_id), self.get('families', family_id)
        if family['hypothesis_id'] != hypothesis_id:
            raise ValueError('Family must belong to declared hypothesis')
        required = {'task', 'universe', 'data', 'evaluation', 'features', 'target', 'model',
                    'primary_metric', 'secondary_metrics', 'baselines', 'randomness', 'stopping', 'criteria', 'code'}
        if not isinstance(contract, dict) or set(contract) != required:
            raise ValueError('Incomplete experiment contract')
        if contract['task'] not in {'binary', 'regression'} or not isinstance(contract['primary_metric'], str) or not contract['primary_metric'].strip():
            raise ValueError('Task and a single primary metric are required')
        if not isinstance(contract['secondary_metrics'], list) or contract['primary_metric'] in contract['secondary_metrics']:
            raise ValueError('Primary and secondary metrics must be distinct')
        for name in ('evaluation', 'features', 'target', 'model', 'randomness', 'stopping', 'criteria', 'code'):
            if not isinstance(contract[name], dict) or not contract[name]:
                raise ValueError(f'{name} must be a nonempty typed contract')
        if not isinstance(contract['baselines'], list) or not contract['baselines']:
            raise ValueError('Declare baseline comparators')
        def reject_runtime_metadata(value):
            if isinstance(value, dict):
                if set(value) & {'created_at', 'updated_at', 'run_id', 'job_id', 'output', 'path'}:
                    raise ValueError('Store volatile runtime metadata in run provenance, not the specification')
                for child in value.values():
                    reject_runtime_metadata(child)
            elif isinstance(value, list):
                for child in value:
                    reject_runtime_metadata(child)
        reject_runtime_metadata(contract)
        if not contract['data'] or not contract['universe']:
            raise ValueError('Exact data identity and universe are required')
        if 'pit_manifest' in contract['data']:
            from .pit.integration import with_pit_inputs
            with_pit_inputs(self.store.path, contract, contract['data']['pit_manifest'])
        doc = {'contract': contract, 'hypothesis_fingerprint': hypothesis['fingerprint'],
               'family_fingerprint': family['fingerprint']}
        return self._register('specs', doc, {'hypothesis_id': hypothesis_id, 'family_id': family_id,
            'registration_mode': 'preregistered', 'primary_metric': contract['primary_metric']})

    def trial(self, *, family_id, parameters):
        dimensions = self.get('families', family_id)['document']['dimensions']
        if set(parameters) != set(dimensions) or any(canonical_json(value) not in {canonical_json(v) for v in dimensions[key]} for key, value in parameters.items()):
            raise ValueError('Trial is outside declared parameter family')
        return self._register('trials', parameters, {'family_id': family_id})

    def run(self, *, spec_id, execution_key, origin='manual', provenance=None):
        if not isinstance(execution_key, str) or not execution_key.strip():
            raise ValueError('An intended execution identity is required')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            specification = self.get('specs', spec_id, db)
            pit = specification['document'].get('contract', {}).get('data', {}).get('pit_manifest')
            if pit is not None and (provenance or {}).get('pit_manifest') != pit:
                raise ValueError('Run PIT provenance must match the frozen specification')
            row = db.execute('SELECT id,spec_id FROM research_runs WHERE execution_key=?', (execution_key,)).fetchone()
            if row:
                if row['spec_id'] != spec_id:
                    raise ValueError('Execution identity is already pinned to a different specification')
                self.event('duplicate_execution', {'execution_key': execution_key}, run_id=row['id'], db=db)
                return self.get('runs', row['id'], db)
            identity = 'run-' + digest(execution_key)
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?)',
                       (identity, spec_id, execution_key, self.clock(), origin, canonical_json(provenance or {})))
            return self.get('runs', identity, db)

    def state(self, run_id, db=None):
        if db is None:
            with self.store.connection() as connection:
                return self.state(run_id, connection)
        self.get('runs', run_id, db)
        row = db.execute('''SELECT o.state FROM research_attempts a LEFT JOIN research_outcomes o ON o.attempt_id=a.id
                            WHERE a.run_id=? ORDER BY a.number DESC LIMIT 1''', (run_id,)).fetchone()
        return ('running' if row[0] is None else row[0]) if row else 'registered'

    def begin(self, run_id, *, resume=False):
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            state = self.state(run_id, db)
            if state != 'registered' and (not resume or state in {'completed', 'running', 'blocked'}):
                raise ValueError(f'Cannot start {state} run; blocked/running work needs reconciliation')
            count = db.execute('SELECT count(*) FROM research_attempts WHERE run_id=?', (run_id,)).fetchone()[0]
            identity = uuid4().hex
            db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,?)',
                       (identity, run_id, count+1, self.clock(), canonical_json({'resume': resume})))
            return self.get('attempts', identity, db)

    def finish(self, attempt_id, *, state, disposition=None, metrics=None, exploratory=None,
               evidence=None, artifacts=(), candidate=None, rule='registry-terminal-v1'):
        if state not in TERMINAL:
            raise ValueError('Invalid terminal state')
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            attempt = self.get('attempts', attempt_id, db)
            run = self.get('runs', attempt['run_id'], db)
            spec = self.get('specs', run['spec_id'], db)
            declared = {spec['primary_metric'], *spec['document'].get('contract', {}).get('secondary_metrics', [])}
            metrics = metrics or {}
            if set(metrics) - declared:
                raise ValueError('Undeclared metrics must be exploratory')
            if any(v is not None and (not isinstance(v, (int, float)) or not math.isfinite(v)) for v in metrics.values()):
                raise ValueError('Metrics must be finite numbers or explicitly unavailable')
            outcome_id = uuid4().hex
            artifact_ids = {}
            for artifact in artifacts:
                artifact_id = 'art-' + digest([attempt_id, artifact['role'], artifact['sha256']])
                db.execute('INSERT INTO research_artifacts VALUES(?,?,?,?,?,?,?)',
                    (artifact_id, run['id'], attempt_id, artifact['role'], artifact['sha256'], artifact['path'], self.clock()))
                artifact_ids[artifact['role']] = artifact_id
            if candidate:
                # Result, candidate registration and provenance link commit together.
                try:
                    self.store._get(db, 'models', candidate['id'])
                except KeyError:
                    self.store._put(db, 'models', candidate)
                db.execute('INSERT OR IGNORE INTO research_model_links VALUES(?,?,?,?,?)',
                    (candidate['id'], 'models', run['id'], artifact_ids['candidate'], self.clock()))
            document = {'metrics': metrics, 'exploratory': exploratory or {}, 'evidence': evidence or {},
                        'rule': rule, 'source': 'deterministic research control', 'artifacts': artifact_ids}
            db.execute('INSERT INTO research_outcomes VALUES(?,?,?,?,?,?,?,?)',
                       (outcome_id, run['id'], attempt_id, self.clock(), state,
                        disposition or ('inconclusive' if state == 'completed' else state),
                        spec['primary_metric'], canonical_json(document)))
            return self.get('outcomes', outcome_id, db)

    def reconcile(self, run_id, *, reason, actor):
        """Explicit local operator decision. Never infer completion from orphan files."""
        meaningful(reason, 'reconciliation reason')
        meaningful(actor, 'reconciliation actor')
        state = self.state(run_id)
        if state == 'running':
            attempt = [a for a in self.list('attempts') if a['run_id'] == run_id][-1]
            return self.finish(attempt['id'], state='blocked', evidence={'reason': reason, 'actor': actor})
        if state != 'blocked':
            raise ValueError('Only interrupted/blocked work can be reconciled')
        # Append a separate operator resolution attempt; SQL still protects completed work.
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            count = db.execute('SELECT count(*) FROM research_attempts WHERE run_id=?', (run_id,)).fetchone()[0]
            identity = uuid4().hex
            db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,?)',
                       (identity, run_id, count+1, self.clock(), canonical_json({'reconciliation': reason, 'actor': actor})))
            run = self.get('runs', run_id, db)
            spec = self.get('specs', run['spec_id'], db)
            db.execute('INSERT INTO research_outcomes VALUES(?,?,?,?,?,?,?,?)',
                       (uuid4().hex, run_id, identity, self.clock(), 'aborted', 'aborted', spec['primary_metric'],
                        canonical_json({'reason': reason, 'actor': actor, 'rule': 'operator-reconciliation-v1'})))
        return {'run_id': run_id, 'state': 'aborted', 'resume_permitted': True}

    def history(self):
        attempts, outcomes = self.list('attempts'), self.list('outcomes')
        return [{**run, 'state': self.state(run['id']),
                 'registration_mode': self.get('specs', run['spec_id'])['registration_mode'],
                 'attempts': [a for a in attempts if a['run_id'] == run['id']],
                 'outcomes': [o for o in outcomes if o['run_id'] == run['id']]}
                for run in self.list('runs')]
