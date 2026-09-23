"""Offline reproductions are verification records, never research executions."""
from contextlib import contextmanager
import fcntl
from hashlib import sha256
import json
import math
from pathlib import Path

from .forecast_identity import canonical_json, digest
from .integrity import VERSION, get_evidence, load_input, reader, rows, verify_run
from .store import utcnow

# Fixed before observing output. Counts/identities/configurations remain exact.
POLICY = {'version': 'float64-v1', 'relative_tolerance': 1e-10, 'absolute_tolerance': 1e-12,
          'exact_fields': ['identity','fingerprints','config','boundaries','selection','columns','count','seed','sha256'],
          'excluded_paths': ['candidate.path', 'checkpoints']}


def compare(recorded, replayed):
    differences = []
    def walk(a, b, path, exact=False):
        exact = exact or any(part in POLICY['exact_fields'] or part.endswith('_sha256') for part in path.split('.'))
        if type(a) is type(b) and isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                p = (path + '.' + key).strip('.')
                if p in POLICY['excluded_paths']:
                    continue
                if key not in a or key not in b:
                    differences.append({'path': p, 'recorded': a.get(key), 'replay': b.get(key), 'status': 'different', 'reason': 'Key absent on one side'})
                else:
                    walk(a[key], b[key], p, exact)
            return
        if type(a) is type(b) and isinstance(a, list):
            if len(a) != len(b):
                differences.append({'path': path + '.length', 'recorded': len(a), 'replay': len(b), 'status': 'different'})
            for i, (left, right) in enumerate(zip(a, b)):
                walk(left, right, f'{path}.{i}', exact)
            return
        status = 'exact' if type(a) is type(b) and a == b else 'different'
        if status != 'exact' and not exact and type(a) is float and type(b) is float and math.isfinite(a) and math.isfinite(b):
            if math.isclose(a, b, rel_tol=POLICY['relative_tolerance'], abs_tol=POLICY['absolute_tolerance']):
                status = 'equivalent'
        differences.append({'path': path, 'recorded': a, 'replay': b, 'status': status})
    walk(recorded, replayed, '')
    status = 'different' if any(d['status'] == 'different' for d in differences) else 'equivalent' if any(d['status'] == 'equivalent' for d in differences) else 'exact'
    return {'status': status, 'policy': POLICY, 'comparisons': differences}


@contextmanager
def exclusive(path):
    with Path(path).open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def reproduction_history(path, run_id=None):
    with reader(path) as db:
        starts = rows(db, 'integrity_reproductions')
        results = {v['reproduction_id']: v for v in rows(db, 'integrity_results')}
    return [{**s, 'result': results.get(s['id']), 'status': results[s['id']]['status'] if s['id'] in results else 'running'}
            for s in starts if run_id is None or s['run_id'] == run_id]


def comparison_report(path, run_id, reproduction_id):
    match = next((v for v in reproduction_history(path, run_id) if v['id'] == reproduction_id), None)
    if match is None:
        raise KeyError(reproduction_id)
    return match


def _execute(path, manifest, output):
    from .experiments import ExperimentConfig, run_experiment
    from .registry_execution import CURRENT, VERIFYING
    contract = manifest['spec']['document']['contract']
    frames = {k: load_input(path, v, manifest['dataset_records'].get(v.get('snapshot_id'))) for k, v in manifest['run']['document']['inputs'].items()}
    token = VERIFYING.set(True)
    execution_token = CURRENT.set(None)
    try:
        return run_experiment(frames['history'], ticker=contract['universe'][0], output=output,
            contexts={k.removeprefix('context:'): v for k,v in frames.items() if k.startswith('context:')},
            feature_sets=tuple(contract['features']['sets']), task=contract['task'],
            config=ExperimentConfig(**contract['evaluation']['config']), include_gru=contract['model']['include_gru'],
            time_budget=contract['stopping']['time_budget_seconds'])
    finally:
        VERIFYING.reset(token)
        CURRENT.reset(execution_token)


def request_reproduction(store, run_id, *, request_key, clock=utcnow):
    """Same key never executes twice. A crashed/pending check is blocked, not retried silently."""
    if not isinstance(request_key, str) or not request_key.strip():
        raise ValueError('Explicit reproduction request key required')
    root = store.path.parent / 'integrity-replays'
    root.mkdir(exist_ok=True)
    identity = 'rep-' + digest([run_id, request_key])
    with exclusive(root / (identity + '.lock')) as owned:
        if not owned:
            return {'id': identity, 'run_id': run_id, 'status': 'blocked', 'reason': 'Verification is still owned by another process'}
        existing = next((r for r in reproduction_history(store.path) if r['request_key'] == request_key), None)
        if existing:
            if existing['run_id'] != run_id:
                raise ValueError('Request key is pinned to another run')
            if existing['result']:
                return existing
            return _finish(store, identity, run_id, 'blocked', {'reason': 'Previous verifier interrupted; use a new explicit request key after inspection'}, clock)
        evidence = get_evidence(store.path, run_id)
        from .registry_execution import code_manifest
        code = code_manifest()
        document = {'mode': 'offline_verification', 'verifier': VERSION, 'policy': POLICY,
                    'manifest_fingerprint': evidence['fingerprint'], 'code_fingerprint': code['sha256'],
                    'environment_fingerprint': digest({'python': code['python'], 'dependencies': code['dependencies']}),
                    'output': str((root / identity).resolve())}
        with store.connection() as db:
            db.execute('INSERT INTO integrity_reproductions VALUES(?,?,?,?,?)',
                       (identity, run_id, request_key, clock(), canonical_json(document)))
        m = evidence['manifest']
        spec = m['spec']
        if not spec or spec['registration_mode'] == 'legacy_import':
            return _finish(store, identity, run_id, 'unavailable', {'reason': 'Legacy or absent preregistration cannot be reconstructed'}, clock)
        contract = spec['document']['contract']
        if contract.get('model', {}).get('family') != 'nested-xgboost-v1':
            return _finish(store, identity, run_id, 'unavailable', {'reason': 'Automatic replay unavailable for ancillary procedures'}, clock)
        if contract.get('code') != code:
            return _finish(store, identity, run_id, 'unavailable', {'reason': 'Registered source/dependency environment differs'}, clock)
        review = verify_run(store.path, run_id, 'artifact', clock=clock)
        if review['status'] == 'invalid':
            status = 'unavailable' if any(c['status'] == 'missing' and c['reference'].startswith('input:') for c in review['checks']) else 'blocked'
            return _finish(store, identity, run_id, status, {'reason': 'Evidence review failed', 'review': review}, clock)
        completed = [o for o in m['outcomes'] if o['state'] == 'completed']
        reports = [a for a in m['artifacts'] if a['role'] == 'report' and any(o['attempt_id'] == a['attempt_id'] for o in completed)]
        if len(completed) != 1 or len(reports) != 1 or review['counts'].get('unverifiable'):
            return _finish(store, identity, run_id, 'unavailable', {'reason': 'Complete unambiguous result and verified evidence required', 'review': review}, clock)
        output = root / identity
        try:
            # Never reuse output from an earlier process/request, even if filenames match.
            output.mkdir(exist_ok=False)
            expected = json.loads(Path(reports[0]['path']).read_text())
            actual = _execute(store.path, m, output)
            if actual.get('status') != 'completed':
                return _finish(store, identity, run_id, 'failed', {'reason': 'Replay did not complete', 'engine_status': actual.get('status')}, clock)
            result = compare(expected, actual)
            for artifact in m['artifacts']:
                if artifact['role'] == 'candidate' and artifact['attempt_id'] == completed[0]['attempt_id']:
                    candidate = actual.get('candidate') or {}
                    actual_path = Path(candidate.get('path', output / 'candidate.joblib'))
                    checksum = sha256(actual_path.read_bytes()).hexdigest()
                    result['comparisons'].append({'path': 'artifact.sha256', 'recorded': artifact['sha256'], 'replay': checksum,
                                                  'status': 'exact' if checksum == artifact['sha256'] else 'different'})
                    if checksum != artifact['sha256']:
                        result['status'] = 'different'
            # Recheck original evidence after execution to detect concurrent file/history changes.
            after = verify_run(store.path, run_id, 'artifact', clock=clock)
            if after['status'] == 'invalid' or get_evidence(store.path, run_id)['fingerprint'] != evidence['fingerprint']:
                return _finish(store, identity, run_id, 'blocked', {'reason': 'Evidence changed during verification', 'review': after}, clock)
            differing = [c for c in result['comparisons'] if c['status'] == 'different']
            result['discrepancy_classification'] = ('ambiguous_artifact_bytes' if differing and all(c['path'] in {'candidate.artifact_sha256','artifact.sha256'} for c in differing)
                                                  else 'invalid_reproduction' if differing else 'expected_numeric_roundoff' if result['status'] == 'equivalent' else 'none')
            result.update(review=review, replay_report_sha256=sha256((output / 'report.json').read_bytes()).hexdigest(),
                          reason='Fixed policy comparison; no research approval or activation')
        except Exception as exc:
            # Database write errors below intentionally propagate, leaving an inspectable start.
            return _finish(store, identity, run_id, 'failed', {'reason': str(exc), 'error_type': type(exc).__name__}, clock)
        return _finish(store, identity, run_id, result['status'], result, clock)


def _finish(store, identity, run_id, status, details, clock):
    start = comparison_report(store.path, run_id, identity)
    certificate = {'reproduction_id': identity, 'run_id': run_id, 'depth': 'replay', 'verifier': VERSION,
                   'manifest_fingerprint': start['document']['manifest_fingerprint'], 'status': status,
                   'policy': POLICY, 'timestamp': clock(), **details}
    certificate['fingerprint'] = digest(certificate)
    with store.connection() as db:
        db.execute('INSERT INTO integrity_results VALUES(?,?,?,?)',
                   (identity, certificate['timestamp'], status, canonical_json(certificate)))
    return comparison_report(store.path, run_id, identity)
