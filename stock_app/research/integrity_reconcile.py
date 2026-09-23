"""Explicit operator cases. Detection is read-only; materialization is opt-in."""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from .forecast_identity import canonical_json, digest
from .integrity import get_evidence, reader, rows, timestamp, verify_run
from .integrity_replay import exclusive, reproduction_history
from .registry import meaningful
from .store import utcnow

STALE_SECONDS = 3 * 60 * 60


def list_cases(path, identity=None):
    with reader(path) as db:
        cases = rows(db, 'integrity_cases')
        actions = rows(db, 'integrity_actions')
    result = []
    for case in cases:
        history = sorted([a for a in actions if a['case_id'] == case['id']], key=lambda a:a['sequence'])
        result.append({**case, 'state': history[-1]['state'] if history else 'open', 'actions': history})
    if identity:
        match = next((c for c in result if c['id'] == identity), None)
        if match is None:
            raise KeyError(identity)
        return match
    return result


def findings(path, *, depth='metadata', clock=utcnow):
    """Age is only a review signal, never proof of worker death."""
    with reader(path) as db:
        runs = rows(db, 'research_runs')
        operations = rows(db, 'operational_runs')
        models = [r['document'] for r in rows(db, 'records') if r['kind'] == 'models']
        all_artifacts = rows(db, 'research_artifacts')
        all_links = rows(db, 'research_model_links')
    found = []
    def add(category, source, related, evidence, severity='error'):
        doc = {'related': related, 'evidence': evidence}
        fingerprint = digest({'category': category, 'source': source, **doc})
        found.append({'id': 'case-' + fingerprint, 'fingerprint': fingerprint, 'category': category,
                      'source': source, 'severity': severity, 'document': doc})
    for run in runs:
        m = get_evidence(path, run['id'])['manifest']
        review = verify_run(path, run['id'], depth, clock=clock)
        for check in review['checks']:
            if check['status'] in {'missing','mismatch'}:
                category = 'checksum_mismatch' if check['status'] == 'mismatch' and check['reference'].startswith(('artifact:', 'input:')) else 'missing_artifact' if check['reference'].startswith('artifact:') else 'provenance_inconsistency'
                add(category, 'research', {'run_id': run['id']}, check)
        if m['spec'] and m['spec']['registration_mode'] == 'legacy_import':
            add('legacy_unverifiable', 'research', {'run_id': run['id']}, {'reason': 'Historical preregistration and input evidence incomplete'}, 'warning')
        open_attempts = [a for a in m['attempts'] if not any(o['attempt_id'] == a['id'] for o in m['outcomes'])]
        for attempt in open_attempts:
            try:
                stale = timestamp(clock()) - timestamp(attempt['created_at']) >= timedelta(seconds=STALE_SECONDS)
            except ValueError:
                stale = False
            if stale:
                related_artifacts = [a for a in m['artifacts'] if a['attempt_id'] == attempt['id']]
                output = run['document'].get('output')
                candidate_present = bool(output and (Path(output) / 'candidate.joblib').exists())
                report_present = bool(output and (Path(output) / 'report.json').is_file())
                report_state = 'not_examined' if report_present else 'absent'
                if report_present and depth == 'artifact':
                    try:
                        report_state = json.loads((Path(output) / 'report.json').read_text()).get('status', 'unknown')
                    except (ValueError, OSError, AttributeError):
                        report_state = 'unreadable'
                artifact_checks = [c for c in review['checks'] if c.get('artifact_id') in {a['id'] for a in related_artifacts}]
                classification = ('ambiguous_evidence' if any(c['status'] in {'missing','mismatch'} for c in artifact_checks)
                                  else 'complete_report_requires_review' if report_state == 'completed'
                                  else 'recorded_artifacts' if related_artifacts else 'unlinked_candidate' if candidate_present
                                  else 'partial_report' if report_present else 'no_recorded_artifact_or_result')
                add('stale_running', 'research', {'run_id': run['id'], 'attempt_id': attempt['id'], 'job_id': run['document'].get('job_id')},
                    {'classification': classification, 'artifacts': [a['id'] for a in related_artifacts],
                     'artifact_statuses': {c['artifact_id']: c['status'] for c in artifact_checks},
                     'result_state': report_state, 'candidate_present': candidate_present, 'policy': 'age-3h-review-only-v1',
                     'worker_death': 'unproven; shared worker lock and operator confirmation required'})
        if any(o['state'] == 'blocked' for o in m['outcomes']) and not open_attempts:
            latest = max(m['attempts'], key=lambda a:a['number'], default=None)
            if latest and any(o['attempt_id'] == latest['id'] and o['state'] == 'blocked' for o in m['outcomes']):
                add('blocked_research', 'research', {'run_id': run['id']}, {'attempt_id': latest['id']})
        output = run['document'].get('output')
        if output and depth == 'artifact':
            for name in ('candidate.joblib','report.json'):
                file = Path(output) / name
                role = 'candidate' if name.endswith('joblib') else 'report'
                if file.is_file():
                    content = file.read_bytes()
                    checksum = sha256(content).hexdigest()
                    # Engine report JSON is pretty-printed; the registry reference is canonical JSON.
                    canonical_checksum = checksum
                    if role == 'report':
                        try:
                            canonical_checksum = digest(json.loads(content))
                        except ValueError:
                            pass
                    if not any(a['role'] == role and a['sha256'] in {checksum, canonical_checksum} for a in m['artifacts']):
                        add('orphan_artifact', 'research', {'run_id': run['id']}, {'path': str(file.resolve()), 'sha256': checksum, 'role': role})
    for model in models:
        run_id = model.get('metadata', {}).get('experiment_run_id')
        if run_id and not any(l['model_id'] == model['id'] and l['run_id'] == run_id for l in all_links):
            add('incomplete_candidate_link', 'research', {'run_id': run_id, 'model_id': model['id']}, {'artifact_sha256': model.get('artifact_sha256')})
        elif model.get('state') == 'candidate' and not any(l['model_id'] == model['id'] for l in all_links):
            add('orphan_candidate_provenance', 'research', {'model_id': model['id']}, {'reason': 'No authoritative originating run identity'}, 'warning')
    referenced = {str(Path(a['path']).resolve()) for a in all_artifacts}
    for file in ((Path(path).parent / 'registry-results').glob('*') if depth == 'artifact' else []):
        if file.is_file() and str(file.resolve()) not in referenced:
            add('orphan_artifact', 'research', {}, {'path': str(file.resolve()), 'sha256': sha256(file.read_bytes()).hexdigest(), 'role': 'unlinked_result'})
    for op in operations:
        if op['status'] == 'blocked':
            related = {'operational_id': op['id']}
            job_id = op['document'].get('outputs', {}).get('legacy_job_id')
            linked = [r['id'] for r in runs if r['document'].get('job_id') == job_id and job_id]
            if len(linked) == 1:
                related['run_id'] = linked[0]
            add('blocked_operational_job', 'operational', related,
                {'reason': op['document'].get('reason'), 'attempt': op['document'].get('attempt')})
    for rep in reproduction_history(path):
        if rep['status'] in {'different','failed','blocked'}:
            add('reproduction_' + rep['status'], 'verification', {'run_id': rep['run_id'], 'reproduction_id': rep['id']},
                {'status': rep['status'], 'certificate': rep['result']['document'].get('fingerprint')})
        elif rep['status'] == 'running' and timestamp(clock()) - timestamp(rep['created_at']) >= timedelta(seconds=STALE_SECONDS):
            add('interrupted_reproduction', 'verification', {'run_id': rep['run_id'], 'reproduction_id': rep['id']}, {'reason': 'Unfinished verifier; inspect process lock before explicit retry'})
    from .forecast_audit import audit_forecasts
    ledger = audit_forecasts(path)
    for item in ledger.get('reconciliation', []):
        add('forecast_legacy_ambiguity', 'forecast', {'legacy_id': item['legacy_id']}, {'reason': item['reason']})
    for error in ledger.get('integrity_errors', []):
        add('forecast_integrity', 'forecast', {}, {'reason': error})
    from .pit.audit import audit as pit_audit
    for issue in pit_audit(path, depth=depth)['issues']:
        add('pit_' + issue['category'], 'pit', {'pit_id': issue['id']}, {'reason': issue['reason']}, issue['severity'])
    return sorted(found, key=lambda f:f['id'])


def detect_cases(store, *, depth='artifact', clock=utcnow):
    detected = findings(store.path, depth=depth, clock=clock)
    with store.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for finding in detected:
            db.execute('INSERT OR IGNORE INTO integrity_cases VALUES(?,?,?,?,?,?,?)',
                       (finding['id'], finding['fingerprint'], finding['category'], finding['source'], finding['severity'], clock(), canonical_json(finding['document'])))
    return list_cases(store.path)


def resolve(store, identity, *, action, actor, reason, evidence=None, clock=utcnow):
    """No arbitrary mutation. State writes and audit action use the same transaction."""
    meaningful(actor, 'actor')
    meaningful(reason, 'reason')
    evidence = evidence or {}
    allowed = {'begin_review','manual_evidence_added','dismissed_false_positive','confirmed_existing_artifact',
               'linked_existing_candidate','run_marked_blocked','retry_authorized','operational_cancelled','confirmed_operational_resolution'}
    if action not in allowed:
        raise ValueError('Unsupported typed action')
    with exclusive(store.path.parent / 'worker.lock') as owned:
        if not owned:
            raise ValueError('Worker lock held; reconciliation refused')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            case = db.execute('SELECT * FROM integrity_cases WHERE id=?', (identity,)).fetchone()
            if not case:
                raise KeyError(identity)
            previous = db.execute('SELECT state,sequence FROM integrity_actions WHERE case_id=? ORDER BY sequence DESC LIMIT 1', (identity,)).fetchone()
            if previous and previous['state'] in {'resolved','dismissed'}:
                raise ValueError('Case already terminal')
            document = json.loads(case['document'])
            related = document['related']
            state = 'resolved'
            if action in {'begin_review','manual_evidence_added'}:
                if action == 'manual_evidence_added' and not evidence:
                    raise ValueError('Explicit evidence required')
                state = 'under_review'
            elif action == 'dismissed_false_positive':
                if not evidence:
                    raise ValueError('Dismissal requires supporting evidence')
                state = 'dismissed'
            elif action == 'confirmed_existing_artifact':
                ref = document['evidence']
                artifact_id = ref.get('artifact_id')
                artifact = db.execute('SELECT * FROM research_artifacts WHERE id=?', (artifact_id,)).fetchone()
                if not artifact or case['category'] not in {'missing_artifact','checksum_mismatch'}:
                    raise ValueError('Case does not identify a registered artifact')
                if sha256(Path(artifact['path']).read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('Artifact still mismatched')
                evidence = {**evidence, 'verified_sha256': artifact['sha256']}
            elif action == 'linked_existing_candidate':
                if case['category'] != 'incomplete_candidate_link':
                    raise ValueError('Candidate linkage case required')
                model_row = db.execute("SELECT document FROM records WHERE kind='models' AND id=?", (related['model_id'],)).fetchone()
                model = json.loads(model_row[0]) if model_row else {}
                if model.get('metadata', {}).get('experiment_run_id') != related['run_id']:
                    raise ValueError('Explicit model run provenance required')
                candidates = list(db.execute("SELECT a.* FROM research_artifacts a JOIN research_outcomes o ON o.attempt_id=a.attempt_id WHERE a.run_id=? AND a.role='candidate' AND o.state='completed' AND a.sha256=?", (related['run_id'], model.get('artifact_sha256'))))
                if len(candidates) != 1 or Path(model.get('artifact', '')).resolve() != Path(candidates[0]['path']).resolve():
                    raise ValueError('Unique completed candidate artifact not proven')
                artifact = candidates[0]
                if sha256(Path(artifact['path']).read_bytes()).hexdigest() != artifact['sha256']:
                    raise ValueError('Candidate checksum mismatch')
                other = db.execute('SELECT run_id FROM research_model_links WHERE model_id=?', (model['id'],)).fetchall()
                if other:
                    raise ValueError('Candidate already linked; review current ownership')
                db.execute('INSERT INTO research_model_links VALUES(?,?,?,?,?)', (model['id'],'models',related['run_id'],artifact['id'],clock()))
                evidence = {**evidence, 'artifact_id': artifact['id'], 'sha256': artifact['sha256']}
            elif action in {'run_marked_blocked','retry_authorized'}:
                if case['category'] not in {'stale_running','blocked_research'} or evidence.get('worker_confirmed_dead') is not True:
                    raise ValueError('Interrupted/blocked case and explicit worker-death confirmation required')
                run_id = related['run_id']
                latest = db.execute('SELECT a.*,o.state FROM research_attempts a LEFT JOIN research_outcomes o ON o.attempt_id=a.id WHERE a.run_id=? ORDER BY a.number DESC LIMIT 1', (run_id,)).fetchone()
                spec = db.execute('SELECT s.primary_metric FROM research_specs s JOIN research_runs r ON r.spec_id=s.id WHERE r.id=?', (run_id,)).fetchone()
                if not latest or not spec:
                    raise ValueError('Run evidence missing')
                if action == 'run_marked_blocked':
                    if latest['state'] is not None:
                        raise ValueError('Run is no longer unfinished')
                    attempt_id, outcome = latest['id'], 'blocked'
                    state = 'under_review'
                else:
                    if latest['state'] != 'blocked' or evidence.get('partial_effects_reviewed') is not True:
                        raise ValueError('Blocked run and explicit partial-effects review required')
                    attempt_id, outcome = uuid4().hex, 'aborted'
                    db.execute('INSERT INTO research_attempts VALUES(?,?,?,?,?)', (attempt_id, run_id, latest['number']+1, clock(), canonical_json({'reconciliation': reason, 'actor': actor, 'case_id': identity})))
                db.execute('INSERT INTO research_outcomes VALUES(?,?,?,?,?,?,?,?)',
                           (uuid4().hex, run_id, attempt_id, clock(), outcome, outcome, spec[0], canonical_json({'rule':'operator-reconciliation-v1','reason':reason,'actor':actor,'case_id':identity})))
            elif action in {'operational_cancelled','confirmed_operational_resolution'}:
                if case['source'] != 'operational':
                    raise ValueError('Operational case required')
                op = db.execute('SELECT document FROM operational_runs WHERE id=?', (related['operational_id'],)).fetchone()
                op = json.loads(op[0]) if op else None
                if not op:
                    raise ValueError('Operational run absent')
                if action == 'confirmed_operational_resolution':
                    if op['status'] not in {'succeeded','cancelled'} or not op.get('reconciliation'):
                        raise ValueError('Phase 0 operator resolution not recorded')
                    evidence = {**evidence, 'phase0_resolution': op['reconciliation']}
                else:
                    if op['status'] not in {'blocked','failed'}:
                        raise ValueError('Operational run no longer blocked/failed')
                    legacy_id = op.get('outputs', {}).get('legacy_job_id')
                    legacy = db.execute("SELECT state FROM records WHERE kind='jobs' AND id=?", (legacy_id,)).fetchone()
                    if legacy and legacy[0] in {'queued','running','cancelling'}:
                        raise ValueError('Finish or cancel referenced legacy job first')
                    op.update(status='cancelled', reconciliation={'at':clock(), 'note':reason, 'previous_status':op['status'], 'actor':actor, 'case_id':identity})
                    from ..jobs.store import JobStore
                    JobStore.save_run(db, op)
            db.execute('INSERT INTO integrity_actions VALUES(?,?,?,?,?,?,?,?,?)',
                       (uuid4().hex, identity, previous['sequence']+1 if previous else 1, action, actor, reason, clock(), state, canonical_json(evidence)))
    return list_cases(store.path, identity)
