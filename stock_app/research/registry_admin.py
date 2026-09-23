"""Read-only registry audit and evidence-only historical import."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .forecast_identity import canonical_json, digest
from .registry import Registry, TABLES


def audit(path, *, verify_files=True):
    """Open SQLite mode=ro: no constructor, schema initialization or recovery."""
    path = Path(path)
    if not path.exists():
        return {'status': 'migration_required', 'counts': {}, 'issues': []}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'research_runs' not in tables:
            return {'status': 'migration_required', 'counts': {}, 'issues': []}
        rows = {name: [dict(r) for r in db.execute(f'SELECT * FROM research_{name}')] for name in TABLES}
        issues = [{'kind': 'foreign_key', 'evidence': list(r)} for r in db.execute('PRAGMA foreign_key_check')]
        specs = {r['id']: r for r in rows['specs']}
        runs = {r['id']: r for r in rows['runs']}
        for name in ('questions', 'hypotheses', 'families', 'specs', 'trials'):
            for row in rows[name]:
                if digest(json.loads(row['document'])) != row['fingerprint']:
                    issues.append({'kind': 'mutated_' + name, 'id': row['id']})
        for outcome in rows['outcomes']:
            run = runs.get(outcome['run_id'])
            spec = specs.get(run['spec_id']) if run else None
            if not spec or outcome['primary_metric'] != spec['primary_metric']:
                issues.append({'kind': 'undeclared_primary_or_orphan_result', 'id': outcome['id']})
        states = Counter()
        for run in rows['runs']:
            attempts = sorted((a for a in rows['attempts'] if a['run_id'] == run['id']), key=lambda a: a['number'])
            outcomes = [o for o in rows['outcomes'] if attempts and o['attempt_id'] == attempts[-1]['id']]
            state = outcomes[-1]['state'] if outcomes else 'running' if attempts else 'registered'
            states[state] += 1
            if state == 'running':
                issues.append({'kind': 'unfinished_attempt', 'run_id': run['id'], 'note': 'May be live; operator must verify ownership before reconciliation'})
        for run in rows['runs']:
            for name, ref in (json.loads(run['document']).get('inputs', {}) if verify_files else {}).items():
                if 'path' in ref:
                    file = Path(ref['path'])
                    if not file.is_file() or sha256(file.read_bytes()).hexdigest() != ref.get('sha256'):
                        issues.append({'kind': 'missing_or_changed_input', 'run_id': run['id'], 'input': name})
            directory = json.loads(run['document']).get('output')
            if directory and verify_files:
                candidate = Path(directory) / 'candidate.joblib'
                if candidate.exists() and not any(a['run_id'] == run['id'] and a['path'] == str(candidate.resolve()) for a in rows['artifacts']):
                    issues.append({'kind': 'orphan_candidate_file', 'run_id': run['id'], 'path': str(candidate)})
        starts = {e['id']: e for e in rows['events'] if e['kind'] in {'fit_started', 'model_fit_started'}}
        ends = {json.loads(e['document']).get('start_event') for e in rows['events']
                if e['kind'] in {'fit_completed', 'fit_failed', 'model_fit_completed', 'model_fit_failed'}}
        for identity, event in starts.items():
            if identity not in ends:
                issues.append({'kind': 'unfinished_fit', 'run_id': event['run_id'], 'event_id': identity})
        for artifact in (rows['artifacts'] if verify_files else []):
            file = Path(artifact['path'])
            if not file.is_file() or sha256(file.read_bytes()).hexdigest() != artifact['sha256']:
                issues.append({'kind': 'missing_or_changed_artifact', 'id': artifact['id'], 'path': str(file)})
        referenced = {r['path'] for r in rows['artifacts']}
        for outcome in rows['outcomes']:
            evidence = json.loads(outcome['document']).get('evidence', {})
            for artifact in evidence.get('orphan_artifacts', []):
                issues.append({'kind': 'uncommitted_artifact', 'run_id': outcome['run_id'], 'path': artifact['path']})
        for file in ((path.parent / 'registry-results').glob('*') if verify_files else []):
            if str(file.resolve()) not in referenced:
                issues.append({'kind': 'orphan_result_file', 'path': str(file)})
        linked = {r['model_id'] for r in rows['model_links']}
        candidates = [r[0] for r in db.execute("SELECT id FROM records WHERE kind='models' AND state='candidate'")]
        missing_candidates = [identity for identity in candidates if identity not in linked]
        for outcome in rows['outcomes']:
            spec = specs.get(runs.get(outcome['run_id'], {}).get('spec_id'))
            doc = json.loads(outcome['document'])
            if spec and spec['registration_mode'] == 'preregistered' and outcome['state'] == 'completed':
                if spec['primary_metric'] not in doc.get('metrics', {}) and not doc.get('evidence', {}).get('metric_location'):
                    issues.append({'kind': 'missing_declared_primary_result', 'run_id': outcome['run_id']})
        trial_events = [r for r in rows['events'] if r['trial_id'] and r['kind'] == 'fit_started']
        families = {family['id']: {'declared_variants': sum(t['family_id'] == family['id'] for t in rows['trials']),
                    'attempted_variants': len({e['trial_id'] for e in trial_events
                        if any(t['id'] == e['trial_id'] and t['family_id'] == family['id'] for t in rows['trials'])})}
                    for family in rows['families']}
        return {'status': 'attention' if issues else 'ok', 'counts': {k: len(v) for k, v in rows.items()},
                'states': dict(states), 'issues': issues, 'families': families,
                'legacy_imported_experiments': sum(s['registration_mode'] == 'legacy_import' for s in rows['specs']),
                'candidates_without_provenance': missing_candidates,
                'duplicate_spec_fingerprints': len(rows['specs']) - len({s['fingerprint'] for s in rows['specs']})}


def import_legacy(registry, *, apply=False):
    """Import only persisted experiment jobs; do not infer missing hypotheses or metrics."""
    jobs = [j for j in registry.store.list('jobs') if j.get('kind') == 'experiment']
    models = registry.store.list('models')
    result = {'mode': 'apply' if apply else 'preview', 'source_jobs': len(jobs), 'imported': 0,
              'already_registered': 0, 'ambiguous': [], 'candidate_links': 0, 'missing_evidence': 0}
    for job in jobs:
        key = 'job:' + job['id']
        existing = [r for r in registry.list('runs') if r['execution_key'] == key]
        if existing:
            result['already_registered'] += 1
            continue
        progress = job.get('progress') or {}
        # The mutable historical job is evidence of its last recorded state, not of every past attempt.
        doc = {'legacy_job_id': job['id'], 'source_sha256': digest(job),
               'limitations': ['No historical preregistration is asserted.',
                              'Earlier attempts may be absent from mutable legacy jobs.'],
               'contract': {'task': job.get('parameters', {}).get('task'), 'data': job.get('snapshots'),
                            'primary_metric': None, 'secondary_metrics': [],
                            'reported_config': progress.get('config'), 'reported_fingerprints': progress.get('fingerprints')}}
        related = [m for m in models if m.get('metadata', {}).get('job_id') == job['id']]
        artifacts, links = [], []
        for model in related:
            file = Path(model['artifact'])
            expected = model.get('artifact_sha256')
            if not file.is_file() or not expected or sha256(file.read_bytes()).hexdigest() != expected:
                result['ambiguous'].append({'job_id': job['id'], 'model_id': model['id'], 'reason': 'artifact evidence unavailable or mismatched'})
                continue
            artifacts.append({'role': 'candidate', 'path': str(file.resolve()), 'sha256': expected})
            links.append(model)
        if not progress:
            result['missing_evidence'] += 1
        if not apply:
            result['imported'] += 1
            result['candidate_links'] += len(links)
            continue
        from .registry_execution import immutable_bytes
        source_ref = immutable_bytes(registry.store.path.parent / 'registry-results', canonical_json(job).encode(), '.json')
        # One transaction imports source evidence, final recorded attempt, result and all model links.
        with registry.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM research_runs WHERE execution_key=?', (key,)).fetchone():
                result['already_registered'] += 1
                continue
            fingerprint = digest(doc)
            spec_id = 'legacy-' + fingerprint
            db.execute('INSERT INTO research_specs VALUES(?,?,?,?,?,?,?,?)',
                (spec_id, fingerprint, registry.clock(), None, None, 'legacy_import', None, canonical_json(doc)))
            run_id = 'run-' + digest(key)
            db.execute('INSERT INTO research_runs VALUES(?,?,?,?,?,?)',
                (run_id, spec_id, key, registry.clock(), 'legacy_import', canonical_json({'job_id': job['id'], 'snapshots': job.get('snapshots')})))
            attempt_id = uuid4().hex
            db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,?)',
                (attempt_id, run_id, 1, registry.clock(), canonical_json({'historical_attempt_count': 'unknown', 'source_state': job.get('state')})))
            state = {'completed': 'completed', 'failed': 'failed', 'cancelled': 'aborted', 'paused': 'aborted'}.get(job.get('state'), 'blocked')
            db.execute('INSERT INTO research_outcomes VALUES(?,?,?,?,?,?,?,?)',
                (uuid4().hex, run_id, attempt_id, registry.clock(), state,
                 'inconclusive' if state == 'completed' else state, None,
                 canonical_json({'rule': 'legacy-evidence-only-v1', 'source': 'historical job',
                                 'exploratory_report': source_ref, 'error': job.get('error'), 'preregistered': False})))
            db.execute('INSERT INTO research_artifacts VALUES(?,?,?,?,?,?,?)',
                ('art-' + digest([run_id, source_ref['sha256']]), run_id, attempt_id, 'legacy_source',
                 source_ref['sha256'], source_ref['path'], registry.clock()))
            for artifact, model in zip(artifacts, links):
                artifact_id = 'art-' + digest([run_id, model['id']])
                db.execute('INSERT INTO research_artifacts VALUES(?,?,?,?,?,?,?)',
                    (artifact_id, run_id, attempt_id, 'candidate', artifact['sha256'], artifact['path'], registry.clock()))
                db.execute('INSERT INTO research_model_links VALUES(?,?,?,?,?)',
                    (model['id'], 'models', run_id, artifact_id, registry.clock()))
            result['imported'] += 1
            result['candidate_links'] += len(links)
    return result
