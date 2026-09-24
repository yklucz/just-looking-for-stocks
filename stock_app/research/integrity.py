"""Read-only evidence inspection. No runtime construction, recovery, or provider IO."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import pickle
import sqlite3

from .forecast_identity import digest, prediction_content
from .registry import TABLES
from .store import utcnow

VERSION = 'research-integrity-v1'


@contextmanager
def reader(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def rows(db, table):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    result = [dict(r) for r in db.execute(f'SELECT * FROM {table} ORDER BY rowid')]
    for row in result:
        if 'document' in row:
            row['document'] = json.loads(row['document'])
    return result


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def get_evidence(path, run_id):
    """Regenerable evidence identity; inspection time and mutable job/model state excluded."""
    with reader(path) as db:
        data = {k: rows(db, 'research_' + k) for k in TABLES}
        run = next((r for r in data['runs'] if r['id'] == run_id), None)
        if run is None:
            raise KeyError(run_id)
        spec = next((s for s in data['specs'] if s['id'] == run['spec_id']), None)
        hypothesis = next((h for h in data['hypotheses'] if spec and h['id'] == spec['hypothesis_id']), None)
        question = next((q for q in data['questions'] if hypothesis and q['id'] == hypothesis['question_id']), None)
        family = next((f for f in data['families'] if spec and f['id'] == spec['family_id']), None)
        links = [v for v in data['model_links'] if v['run_id'] == run_id]
        forecasts = []
        revisions = {v['id']: v for v in rows(db, 'forecast_revisions')}
        for issuance in rows(db, 'forecast_issuances'):
            if issuance['model_id'] in {v['model_id'] for v in links}:
                # Outcome resolution is mutable; authority and original prediction are not.
                forecasts.append({k: issuance[k] for k in ('id','identity_json','model_id','origin','authoritative_revision_id')}
                                 | {'revision': revisions.get(issuance['authoritative_revision_id'])})
        contract = spec['document'].get('contract', {}) if spec else {}
        snapshot_ids = {ref['snapshot_id'] for ref in run['document'].get('inputs', {}).values() if 'snapshot_id' in ref}
        dataset_records = {v['id']: {k: v['document'].get(k) for k in
                           ('id','symbol','path','sha256','raw_path','raw_sha256','rows','first_session','last_session','created_at','recorded_at')}
                           for v in rows(db, 'records') if v['kind'] == 'datasets' and v['id'] in snapshot_ids}
        manifest = {'version': VERSION, 'dataset_records': dataset_records, 'run': run, 'spec': spec, 'hypothesis': hypothesis,
                    'question': question, 'family': family,
                    'trials': sorted([t for t in data['trials'] if family and t['family_id'] == family['id']], key=lambda x:x['id']),
                    'model_links': sorted(links, key=lambda x:x['model_id']), 'forecasts': sorted(forecasts, key=lambda x:x['id']),
                    'contracts': {name: {'value': contract.get(name), 'fingerprint': digest(contract[name]) if name in contract else None}
                                  for name in ('data','features','target','randomness','model','evaluation','code','primary_metric','secondary_metrics','baselines')}}
        for kind in ('attempts', 'outcomes', 'artifacts', 'events'):
            # Registration/duplicate-use events do not define execution evidence.
            values = [v for v in data[kind] if v['run_id'] == run_id and
                      (kind != 'events' or v['kind'] in {'fit_started','fit_completed','fit_failed','model_fit_started','model_fit_completed','model_fit_failed','validation_metric'})]
            manifest[kind] = sorted(values, key=lambda x: x['id'])
    return {'manifest': manifest, 'fingerprint': digest(manifest)}


def file_status(ref, *, deep):
    if not ref.get('path') or not ref.get('sha256'):
        return 'unverifiable', 'No recorded path/checksum'
    if not deep:
        return 'unverifiable', 'Bytes not checked at metadata depth'
    try:
        content = Path(ref['path']).read_bytes()
    except FileNotFoundError:
        return 'missing', 'Referenced file is absent'
    except OSError as exc:
        return 'unverifiable', str(exc)
    return ('verified', 'SHA-256 matches') if sha256(content).hexdigest() == ref['sha256'] else ('mismatch', 'SHA-256 differs')


def load_input(path, ref, recorded=None):
    """Read trusted local snapshot/pickle without constructing a writing repository."""
    if 'snapshot_id' in ref:
        root = Path(path).parent / 'datasets'
        matches = []
        for file in root.glob('*/*/metadata.json'):
            value = json.loads(file.read_text())
            if value.get('id') == ref['snapshot_id']:
                matches.append((file, value))
        if not matches:
            raise FileNotFoundError('Snapshot manifest absent: ' + str(ref['snapshot_id']))
        if len(matches) != 1:
            raise ValueError('Snapshot identity resolves ambiguously')
        file, meta = matches[0]
        if recorded is not None and any(recorded.get(k) is not None and recorded[k] != meta.get(k) for k in ('id','path','sha256','raw_path','raw_sha256','rows')):
            raise ValueError('Snapshot manifest disagrees with its database identity/checksums')
        if file.parent.name != meta['id'] or meta['status'] != 'valid':
            raise ValueError('Snapshot identity/status mismatch')
        for key, checksum in (('path','sha256'), ('raw_path','raw_sha256')):
            p = Path(meta[key]).resolve()
            if not p.is_relative_to(file.parent.resolve()):
                raise ValueError('Snapshot file belongs to another identity')
            if sha256(p.read_bytes()).hexdigest() != meta[checksum]:
                raise ValueError('Snapshot checksum mismatch')
        import pandas as pd
        frame = pd.read_csv(meta['path'], index_col='Session', parse_dates=['Session'])
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame['AvailableAt'] = pd.to_datetime(frame['AvailableAt'], utc=True)
        if len(frame) != meta['rows']:
            raise ValueError('Snapshot row count mismatch')
        return frame
    content = Path(ref['path']).read_bytes()
    if sha256(content).hexdigest() != ref['sha256']:
        raise ValueError('Input checksum mismatch')
    return pickle.loads(content)


def verify_run(path, run_id, depth='metadata', *, clock=utcnow):
    if depth not in {'metadata', 'artifact'}:
        raise ValueError('Use request_reproduction for explicit replay depth')
    evidence = get_evidence(path, run_id)
    m, checks = evidence['manifest'], []
    deep = depth == 'artifact'
    def add(name, status, reason, **details):
        checks.append({'reference': name, 'status': status, 'reason': reason, **details})
    def check(name, ok, reason, **details):
        add(name, 'verified' if ok else 'mismatch', reason, **details)
    run, spec = m['run'], m['spec']
    if not spec:
        add('spec', 'missing', 'Run specification is absent')
        contract, legacy = {}, True
    else:
        contract = spec['document'].get('contract', {})
        legacy = spec['registration_mode'] == 'legacy_import'
    for name in ('question','hypothesis','family','spec'):
        value = m[name]
        if value:
            check(name, digest(value['document']) == value['fingerprint'], 'Stored content fingerprint', id=value['id'])
        elif name != 'spec':
            add(name, 'unverifiable' if legacy else 'missing', 'Historical provenance was not recorded' if legacy else 'Required record absent')
    if m['hypothesis'] and m['question']:
        check('hypothesis.question', m['hypothesis']['document'].get('question_fingerprint') == m['question']['fingerprint'], 'Parent identity')
    if m['family'] and m['hypothesis'] and spec:
        check('spec.ownership', m['family']['hypothesis_id'] == spec['hypothesis_id'] and
              spec['document'].get('hypothesis_fingerprint') == m['hypothesis']['fingerprint'] and
              spec['document'].get('family_fingerprint') == m['family']['fingerprint'] and
              m['family']['document'].get('hypothesis_fingerprint') == m['hypothesis']['fingerprint'], 'Family/hypothesis ownership')
    for trial in m['trials']:
        dims = m['family']['document']['dimensions']
        parameters = trial['document']
        check('trial:' + trial['id'], digest(parameters) == trial['fingerprint'] and set(parameters) == set(dims) and
              all(parameters[k] in dims[k] for k in parameters), 'Declared trial realization')
    for name, entry in m['contracts'].items():
        add('contract:' + name, 'verified' if entry['value'] is not None and not legacy else 'unverifiable',
            'Contract is recorded; does not assert execution matched it' if not legacy else 'No historical preregistration asserted')
    if contract.get('task') == 'regression':
        threshold_status = 'not_applicable'
    else:
        threshold_status = 'verified' if contract.get('target', {}).get('event_threshold') is not None else 'unverifiable'
    add('classification_threshold', threshold_status, 'Task-specific target contract')
    if spec and not legacy:
        check('spec.primary_metric', spec['primary_metric'] == contract.get('primary_metric'), 'Declared metric identity')
    attempts = {a['id']: a for a in m['attempts']}
    artifacts = {a['id']: a for a in m['artifacts']}
    def ordered(name, before, after):
        try:
            check(name, timestamp(before) <= timestamp(after), 'Causal chronology')
        except (ValueError, TypeError, AttributeError):
            add(name, 'unverifiable', 'Invalid or absent timestamp')
    if spec:
        ordered('chronology:registration', spec['created_at'], run['created_at'])
    for a in m['attempts']:
        ordered('chronology:attempt:' + a['id'], run['created_at'], a['created_at'])
    numbers = sorted(a['number'] for a in m['attempts'])
    check('attempt.sequence', numbers == list(range(1, len(numbers)+1)), 'Contiguous attempt sequence')
    for outcome in m['outcomes']:
        a = attempts.get(outcome['attempt_id'])
        check('result.ownership:' + outcome['id'], a is not None and a['run_id'] == run_id, 'Result belongs to run/attempt')
        if a:
            ordered('chronology:result:' + outcome['id'], a['created_at'], outcome['created_at'])
        metrics = outcome['document'].get('metrics', {})
        declared = {contract.get('primary_metric'), *contract.get('secondary_metrics', [])}
        check('result.metrics:' + outcome['id'], spec is not None and outcome['primary_metric'] == spec['primary_metric'] and not (set(metrics)-declared), 'Declared metric/spec linkage')
        if outcome['state'] == 'completed' and not legacy:
            add('result.primary:' + outcome['id'], 'verified' if metrics.get(spec['primary_metric']) is not None else 'unverifiable', 'Scalar primary measurement availability')
            add('result.report:' + outcome['id'], 'verified' if any(a['attempt_id'] == outcome['attempt_id'] and a['role'] == 'report' for a in m['artifacts']) else 'missing', 'Completed result report reference')
        for role, identity in outcome['document'].get('artifacts', {}).items():
            artifact = artifacts.get(identity)
            check('result.artifact:' + identity, artifact is not None and artifact['role'] == role and artifact['attempt_id'] == outcome['attempt_id'], 'Result/artifact identity')
    for previous, following in zip(sorted(m['attempts'], key=lambda a:a['number']), sorted(m['attempts'], key=lambda a:a['number'])[1:]):
        terminal = [o for o in m['outcomes'] if o['attempt_id'] == previous['id']]
        check('attempt.transition:' + following['id'], len(terminal) == 1 and terminal[0]['state'] != 'completed', 'Previous attempt must be terminal and non-completed')
        if terminal:
            ordered('chronology:retry:' + following['id'], terminal[0]['created_at'], following['created_at'])
    for event in m['events']:
        a = attempts.get(event['attempt_id'])
        check('event.ownership:' + event['id'], a is not None and (event['trial_id'] is None or event['trial_id'] in {t['id'] for t in m['trials']}), 'Event attempt/trial scope')
        if a:
            ordered('chronology:event:' + event['id'], a['created_at'], event['created_at'])
        if event['kind'] == 'fit_completed' and event['document'].get('checkpoint'):
            output = run['document'].get('output')
            if output:
                ref = {'path': str(Path(output) / 'checkpoints' / (event['document']['checkpoint'] + '.joblib')),
                       'sha256': event['document'].get('sha256')}
                status, reason = file_status(ref, deep=deep)
                add('checkpoint:' + event['id'], status, reason)
            else:
                add('checkpoint:' + event['id'], 'unverifiable', 'Checkpoint output location not recorded')
    for artifact in m['artifacts']:
        check('artifact.ownership:' + artifact['id'], artifact['attempt_id'] in attempts, 'Artifact attempt scope')
        status, reason = file_status(artifact, deep=deep)
        add('artifact:' + artifact['id'], status, reason, artifact_id=artifact['id'], role=artifact['role'])
        if deep and status == 'verified' and artifact['role'] in {'report','legacy_source'}:
            try:
                report = json.loads(Path(artifact['path']).read_text())
                if artifact['role'] == 'report':
                    outcome = next((o for o in m['outcomes'] if o['attempt_id'] == artifact['attempt_id']), None)
                    check('report.metrics:' + artifact['id'], outcome is not None and all(report.get('aggregate', {}).get(k) == v for k,v in outcome['document'].get('metrics', {}).items()), 'Report matches recorded measurements')
                    candidate = report.get('candidate')
                    if isinstance(candidate, dict):
                        saved = [a for a in m['artifacts'] if a['attempt_id'] == artifact['attempt_id'] and a['role'] == 'candidate']
                        check('report.candidate:' + artifact['id'], len(saved) == 1 and
                              candidate.get('artifact_sha256') == saved[0]['sha256'] and
                              Path(candidate.get('path', '')).resolve() == Path(saved[0]['path']).resolve(), 'Candidate report/artifact identity')
                    if report.get('fingerprints') and not legacy:
                        inputs = report['fingerprints']['inputs']
                        check('report.inputs:' + artifact['id'], inputs['history'] == contract['data']['history']['frame_sha256'] and
                              inputs['config'] == contract['evaluation']['config'] and inputs['task'] == contract['task'] and
                              inputs.get('contexts', {}) == {k.removeprefix('context:'): v['frame_sha256'] for k,v in contract['data'].items() if k.startswith('context:')} and
                              inputs.get('feature_sets') == contract['features']['sets'], 'Report data/config/task/feature identity')
            except (ValueError, KeyError, TypeError) as exc:
                add('report:' + artifact['id'], 'mismatch', 'Unreadable or inconsistent report: ' + str(exc))
    pit_manifest = contract.get('data', {}).get('pit_manifest') if isinstance(contract.get('data'), dict) else None
    if pit_manifest is not None:
        from .pit.integration import evidence_checks
        checks.extend(evidence_checks(path, pit_manifest, depth=depth))
        check('pit.run_manifest', run['document'].get('pit_manifest') == pit_manifest, 'Run/spec PIT identity')
    frame_data = {k:v for k,v in (contract.get('data') or {}).items() if k != 'pit_manifest'}
    refs = run['document'].get('inputs', {})
    if legacy:
        add('inputs', 'unverifiable', 'Legacy inputs not preregistered')
    else:
        check('input.identities', set(refs) == set(frame_data), 'Exact input reference set')
    for name in sorted(set(refs) | set(frame_data)):
        ref, expected = refs.get(name), frame_data.get(name)
        if not isinstance(expected, dict) or not expected.get('frame_sha256'):
            add('input:' + name, 'unverifiable', 'No registered frame fingerprint')
        elif not ref:
            add('input:' + name, 'missing', 'Input reference absent')
        elif not deep:
            add('input:' + name, 'unverifiable', 'Content not checked at metadata depth')
        else:
            try:
                from .experiments import _frame_hash
                frame = load_input(path, ref, m['dataset_records'].get(ref.get('snapshot_id')))
                if 'snapshot_id' in ref:
                    recorded = m['dataset_records'].get(ref['snapshot_id'])
                    if recorded is None:
                        add('snapshot.record:' + name, 'unverifiable', 'Snapshot database checksum record absent; only registered frame identity can be verified')
                    else:
                        for field, checksum in (('path','sha256'), ('raw_path','raw_sha256')):
                            status, reason = file_status({'path': recorded.get(field), 'sha256': recorded.get(checksum)}, deep=True)
                            add('snapshot.record:' + name + ':' + field, status, reason)
                check('input:' + name, _frame_hash(frame) == expected['frame_sha256'] and
                      (expected.get('rows') is None or len(frame) == expected['rows']), 'Frame identity and sample count')
            except FileNotFoundError as exc:
                add('input:' + name, 'missing', str(exc))
            except (ValueError, KeyError, TypeError, EOFError, pickle.UnpicklingError) as exc:
                add('input:' + name, 'mismatch', str(exc))
            except OSError as exc:
                add('input:' + name, 'unverifiable', str(exc))
    with reader(path) as db:
        models = {r['id']: r['document'] for r in rows(db, 'records') if r['kind'] == 'models'}
    for link in m['model_links']:
        artifact, model = artifacts.get(link['artifact_id']), models.get(link['model_id'])
        check('candidate:' + link['model_id'], artifact is not None and artifact['role'] == 'candidate' and model is not None and
              model.get('artifact_sha256') == artifact['sha256'] and
              Path(model.get('artifact', '')).resolve() == Path(artifact['path']).resolve() and
              model.get('metadata', {}).get('experiment_run_id', run_id) == run_id,
              'Candidate run, artifact, checksum and path ownership')
    for model_id, model in models.items():
        if model.get('metadata', {}).get('experiment_run_id') == run_id and model_id not in {v['model_id'] for v in m['model_links']}:
            add('candidate:' + model_id, 'missing', 'Candidate relational provenance link absent', model_id=model_id)
    for forecast in m['forecasts']:
        revision = forecast['revision']
        identity = json.loads(forecast['identity_json'])
        model = models.get(forecast['model_id'], {})
        check('forecast:' + forecast['id'], revision is not None and revision['issuance_id'] == forecast['id'] and
              identity.get('model_artifact_sha256') == model.get('artifact_sha256'), 'Authoritative revision and candidate checksum')
        if revision:
            doc = revision['document']
            check('forecast.fingerprint:' + forecast['id'], forecast['id'] == 'fc-' + digest(identity) and
                  revision['id'] == forecast['authoritative_revision_id'] and
                  digest(prediction_content(doc['payload'])) == revision['output_hash'] and
                  revision['input_key'] == digest({'snapshot_id': doc['snapshot_id'], 'snapshots': doc['payload'].get('snapshots'), 'input_sha256': doc['payload'].get('input_sha256')}), 'Canonical output/input identity')
            if not legacy:
                check('forecast.target:' + forecast['id'], identity['target_definition'].get('task') == contract.get('task') and
                      identity['horizon'] == contract.get('target', {}).get('horizon'), 'Forecast/research target identity')
            source = doc.get('provenance', {})
            if not source.get('sources'):
                add('forecast.sources:' + forecast['id'], 'unverifiable', 'Original source checksums/availability not recorded')
            if source.get('model_artifact_sha256'):
                check('forecast.model:' + forecast['id'], source['model_artifact_sha256'] == model.get('artifact_sha256'), 'Recorded forecast model provenance')
            else:
                add('forecast.model:' + forecast['id'], 'unverifiable', 'Original forecast model fingerprint absent')
            if doc.get('kind') == 'prospective':
                from .ledger import session_times
                try:
                    close, next_open, _, _ = session_times(forecast['origin'], identity['horizon'])
                    check('forecast.issuance:' + forecast['id'], close.to_pydatetime() <= timestamp(doc['issued_at']) < next_open.to_pydatetime(), 'Prospective issuance window')
                except (ValueError, TypeError):
                    add('forecast.issuance:' + forecast['id'], 'mismatch', 'Invalid prospective timestamp')
                for symbol, source_ref in source.get('sources', {}).items():
                    available = source_ref.get('downloaded_at') or source_ref.get('created_at')
                    if available:
                        ordered('forecast.source:' + forecast['id'] + ':' + symbol, available, doc['issued_at'])
                    else:
                        add('forecast.source:' + forecast['id'] + ':' + symbol, 'unverifiable', 'Source availability not recorded')
            cutoff = model.get('metadata', {}).get('candidate', {}).get('active_cutoff')
            if cutoff:
                ordered('forecast.cutoff:' + forecast['id'], cutoff, doc['issued_at'])
                try:
                    check('forecast.origin:' + forecast['id'], timestamp(cutoff).date() < datetime.fromisoformat(forecast['origin']).date(), 'Forecast origin follows model fitting cutoff')
                except ValueError:
                    add('forecast.origin:' + forecast['id'], 'unverifiable', 'Invalid cutoff/origin timestamp')
            else:
                add('forecast.cutoff:' + forecast['id'], 'unverifiable', 'Fitting cutoff unavailable')
    counts = dict(Counter(c['status'] for c in checks))
    if counts.get('missing', 0) + counts.get('mismatch', 0):
        status = 'invalid'
    else:
        status = 'incomplete' if counts.get('unverifiable') else 'verified'
    certificate = {'version': VERSION, 'run_id': run_id, 'manifest_fingerprint': evidence['fingerprint'],
                   'depth': depth, 'status': status, 'checks': checks, 'counts': counts, 'timestamp': clock()}
    return {**certificate, 'fingerprint': digest(certificate)}
