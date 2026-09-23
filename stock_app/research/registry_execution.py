"""Preregister existing candidate computations; do not change their mathematics."""
from contextvars import ContextVar
from dataclasses import asdict
from functools import wraps
from hashlib import sha256
from itertools import product
import json
import importlib.metadata
import platform
import os
from pathlib import Path
import re
import tempfile

from .forecast_identity import canonical_json, digest
from .registry import Registry
from .store import ResearchStore

CURRENT = ContextVar('research_execution', default=None)
VERIFYING = ContextVar('research_verification', default=False)


def code_manifest():
    root = Path(__file__).resolve().parents[1]
    paths = sorted({root / 'config.py', root / 'research/experiments.py', root / 'research/statistics.py',
                    *(p for folder in ('training', 'models', 'features', 'targets', 'evaluation') for p in (root / folder).rglob('*.py'))})
    files = {str(p.relative_to(root)): sha256(p.read_bytes()).hexdigest() for p in paths}
    versions = {name: importlib.metadata.version(name) for name in ('numpy', 'pandas', 'scikit-learn', 'xgboost', 'torch')}
    return {'sha256': digest(files), 'files': files, 'dependencies': versions, 'python': platform.python_version()}


def immutable_bytes(root, content, suffix):
    """Content addressing + atomic no-overwrite publication. Orphans are auditable."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fingerprint = sha256(content).hexdigest()
    path = root / (fingerprint + suffix)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    if sha256(path.read_bytes()).hexdigest() != fingerprint:
        raise ValueError('Immutable registry artifact checksum mismatch')
    return {'path': str(path.resolve()), 'sha256': fingerprint}


def default_registry():
    root = Path(os.environ.get('STOCK_RESEARCH_ROOT', Path(__file__).resolve().parents[2] / 'artifacts/research'))
    return Registry(ResearchStore(root / 'research.sqlite3'))


def prepare(registry, history, options, snapshots=None, registered_spec_id=None):
    from .experiments import ExperimentConfig, _frame_hash, _boundary_metadata, outer_splits
    from ..config import FeatureConfig
    import pickle
    config = options.get('config') or ExperimentConfig()
    task = options.get('task', 'binary')
    features = list(options.get('feature_sets', ('baseline', 'context')))
    metric = 'brier' if task == 'binary' else 'mae'
    inputs, refs = {}, {}
    for symbol, frame in {'history': history, **{'context:' + k: v for k, v in (options.get('contexts') or {}).items()}}.items():
        source_symbol = options['ticker'] if symbol == 'history' else symbol.removeprefix('context:')
        if frame is None:
            inputs[symbol] = {'snapshot_id': (snapshots or {}).get(source_symbol), 'frame_sha256': None}
            continue
        inputs[symbol] = {'frame_sha256': _frame_hash(frame), 'rows': len(frame),
                          'first': str(frame.index[0]) if len(frame) else None,
                          'last': str(frame.index[-1]) if len(frame) else None}
        if snapshots and source_symbol in snapshots:
            refs[symbol] = {'snapshot_id': snapshots[source_symbol]}
        else:
            refs[symbol] = immutable_bytes(registry.store.path.parent / 'registry-inputs',
                                           pickle.dumps(frame, protocol=5), '.pickle')
    boundaries = []
    boundary_error = None
    try:
        boundaries = [_boundary_metadata(split, history, config) for split in outer_splits(len(history), config)]
    except (ValueError, TypeError, IndexError) as exc:
        # Validation belongs to the unchanged engine, including invalid experiments.
        boundary_error = str(exc)
    contract = {'task': task, 'universe': [options['ticker']], 'data': inputs,
        'evaluation': {'config': asdict(config), 'outer_boundaries': boundaries,
                       'boundary_validation': boundary_error, 'purge_sessions': config.horizon,
                       'selection_metric': 'log_loss' if task == 'binary' else 'mean_squared_error',
                       'bootstrap': {'method': 'paired moving blocks', 'block_size': 20,
                                     'confidence': .95, 'resamples': config.bootstrap_resamples},
                       'aggregation': 'equal weight per outer fold'},
        'features': {'sets': features, 'config': asdict(FeatureConfig())},
        'target': {'measure': 'adjusted_close_log_return', 'task': task, 'horizon': config.horizon,
                   'event_threshold': config.event_threshold},
        'model': {'family': 'nested-xgboost-v1', 'include_gru': options.get('include_gru', False)},
        'primary_metric': metric,
        'secondary_metrics': ['log_loss', 'auc', 'balanced_accuracy'] if task == 'binary' else
                             ['rmse', 'price_mae', 'price_rmse', 'interval_coverage', 'mean_interval_width', 'nominal_coverage'],
        'baselines': ['training_prior', 'majority', 'momentum', 'logistic'] if task == 'binary' else
                     ['unchanged_price', 'training_mean'],
        'randomness': {'seed': config.seed},
        'stopping': {'time_budget_seconds': options.get('time_budget', 7200), 'cancellation': 'operator requested',
                     'early_stopping_rounds': config.early_stopping_rounds, 'n_estimators': config.n_estimators},
        'criteria': {'rule': 'descriptive-baseline-direction-v1',
                     'comparator': 'training_prior' if task == 'binary' else 'unchanged_price',
                     'supported': 'all outer-fold bootstrap lower bounds above zero',
                     'not_supported': 'mean outer-fold improvement <= zero',
                     'otherwise': 'inconclusive; no eligibility or promotion decision'},
        'code': code_manifest()}
    # Normalize tuple/list representation through the existing canonical serializer.
    contract = json.loads(canonical_json(contract))
    if registered_spec_id:
        spec = registry.get('specs', registered_spec_id)
        if spec['registration_mode'] != 'preregistered' or spec['document']['contract'] != contract:
            raise ValueError('Registered specification does not match the actual execution contract')
        family = registry.family(hypothesis_id=spec['hypothesis_id'], dimensions={
            'feature_set': features, 'window': list(config.windows), 'parameters': list(config.settings)})
        if family['id'] != spec['family_id']:
            raise ValueError('Registered parameter family does not match execution')
    else:
        question = registry.question(title=f'{task} candidate comparison for {options["ticker"]}',
            description='Does the fixed nested chronological candidate procedure improve held-out baseline loss?',
            creator='existing candidate workflow v1')
        hypothesis = registry.hypothesis(question_id=question['id'],
            statement=f'The declared candidate procedure reduces held-out {metric} against the declared baseline.',
            effect='Lower held-out primary loss.', rationale='Compare the existing selection procedure against causal simple baselines.',
            falsification='Nonpositive baseline-minus-candidate primary loss improvement falsifies the expected direction.')
        family = registry.family(hypothesis_id=hypothesis['id'], dimensions={
            'feature_set': features, 'window': list(config.windows), 'parameters': list(config.settings)})
        spec = registry.spec(hypothesis_id=hypothesis['id'], family_id=family['id'], contract=contract)
    trials = {}
    for feature, (wi, window), (si, parameters) in product(features, enumerate(config.windows), enumerate(config.settings)):
        trial = registry.trial(family_id=family['id'], parameters={'feature_set': feature, 'window': window, 'parameters': parameters})
        trials[f'{feature}-w{wi}-s{si}'] = trial['id']
    return spec, refs, trials


def checkpoint_call(checkpoints, key, callback):
    context = CURRENT.get()
    if context is None:
        return callback()
    registry, run, attempt, trials = context
    match = re.search(r'(baseline|context)-w\d+-s\d+$', key)
    trial_id = trials.get(match.group(0)) if match else None
    ref = {'checkpoint': key, 'cached': (checkpoints.output / 'checkpoints' / (key + '.joblib')).exists()}
    event_id = registry.event('fit_started', ref, run_id=run['id'], attempt_id=attempt['id'], trial_id=trial_id)
    try:
        result = callback()
    except BaseException as exc:
        registry.event('fit_failed', {**ref, 'start_event': event_id, 'error_type': type(exc).__name__, 'error': str(exc)},
                       run_id=run['id'], attempt_id=attempt['id'], trial_id=trial_id)
        raise
    path = checkpoints.output / 'checkpoints' / (key + '.joblib')
    registry.event('fit_completed', {**ref, 'start_event': event_id, 'sha256': sha256(path.read_bytes()).hexdigest()},
                   run_id=run['id'], attempt_id=attempt['id'], trial_id=trial_id)
    return result


def record_selection(key, info):
    context = CURRENT.get()
    if context:
        registry, run, attempt, trials = context
        match = re.search(r'(baseline|context)-w\d+-s\d+$', key)
        registry.event('validation_metric', {'checkpoint': key, **info}, run_id=run['id'],
                       attempt_id=attempt['id'], trial_id=trials.get(match.group(0)) if match else None)


def execute(engine, history, *, registry=None, execution_key=None, origin='manual', snapshots=None,
            job_id=None, registered_spec_id=None, **options):
    registry = registry or default_registry()
    spec, refs, trials = prepare(registry, history, options, snapshots, registered_spec_id)
    key = execution_key or 'directory:' + digest([str(Path(options['output']).resolve()), spec['fingerprint']])
    run = registry.run(spec_id=spec['id'], execution_key=key, origin=origin,
                       provenance={'inputs': refs, 'snapshots': snapshots or {}, 'job_id': job_id,
                                   'output': str(Path(options['output']).resolve())})
    if registry.state(run['id']) == 'completed':
        registry.event('completed_execution_reused', {}, run_id=run['id'])
        completed = next(o for o in registry.list('outcomes') if o['run_id'] == run['id'] and o['state'] == 'completed')
        artifacts = [a for a in registry.list('artifacts') if a['attempt_id'] == completed['attempt_id'] and a['role'] == 'report']
        if len(artifacts) != 1:
            raise ValueError('Completed report is ambiguous; reconciliation required')
        for saved in registry.list('artifacts'):
            if saved['attempt_id'] == completed['attempt_id']:
                if sha256(Path(saved['path']).read_bytes()).hexdigest() != saved['sha256']:
                    raise ValueError('Completed artifact checksum mismatch')
        artifact = artifacts[0]
        content = Path(artifact['path']).read_bytes()
        if sha256(content).hexdigest() != artifact['sha256']:
            raise ValueError('Completed report checksum mismatch')
        return json.loads(content)
    attempt = registry.begin(run['id'], resume=registry.state(run['id']) != 'registered')
    token = CURRENT.set((registry, run, attempt, trials))
    try:
        try:
            existing_report = Path(options['output']) / 'report.json'
            if existing_report.exists() and json.loads(existing_report.read_text()).get('status') == 'completed':
                raise ValueError('Existing completed output predates this execution fingerprint; import legacy evidence or use a new directory')
            report = engine(history, **options)
        except BaseException as exc:
            state = 'aborted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'invalid' if isinstance(exc, ValueError) else 'failed'
            registry.finish(attempt['id'], state=state, evidence={'error_type': type(exc).__name__, 'error': str(exc)})
            raise
        state = 'completed' if report.get('status') == 'completed' else 'aborted'
        refs_out = []
        report_ref = immutable_bytes(registry.store.path.parent / 'registry-results', canonical_json(report).encode(), '.json')
        refs_out.append({'role': 'report', **report_ref})
        candidate_record = None
        candidate = report.get('candidate')
        if candidate and state == 'completed':
            path = Path(candidate['path'] if isinstance(candidate, dict) else candidate)
            fingerprint = sha256(path.read_bytes()).hexdigest()
            refs_out.append({'role': 'candidate', 'path': str(path.resolve()), 'sha256': fingerprint})
            if job_id:
                candidate_record = {'id': fingerprint, 'symbol': options['ticker'], 'task': options.get('task', 'binary'),
                    'state': 'candidate', 'artifact': str(path), 'artifact_sha256': fingerprint,
                    'metadata': {'job_id': job_id, 'snapshots': snapshots or {}, 'candidate': candidate,
                                 'experiment_run_id': run['id'], 'experiment_spec_id': spec['id'],
                                 'evaluation_kind': 'historical_replay'}}
        primary = spec['primary_metric']
        declared = {primary, *spec['document']['contract']['secondary_metrics']}
        metrics = {key: value for key, value in report.get('aggregate', {}).items() if key in declared}
        comparisons = [fold.get('comparisons', {}).get(spec['document']['contract']['criteria']['comparator'])
                       for fold in report.get('folds', [])]
        disposition = 'inconclusive'
        if comparisons and all(c is not None for c in comparisons):
            if sum(c['mean_improvement'] for c in comparisons) <= 0:
                disposition = 'not_supported'
            elif all(c['lower'] > 0 for c in comparisons):
                disposition = 'supported'
        try:
            registry.finish(attempt['id'], state=state, disposition=disposition if state == 'completed' else state,
                metrics=metrics, evidence={'engine_status': report.get('status'), 'comparisons': comparisons,
                    'primary_available': primary in metrics, 'report': report_ref},
                exploratory={k: v for k, v in report.get('aggregate', {}).items() if k not in declared},
                artifacts=refs_out, candidate=candidate_record, rule='descriptive-baseline-direction-v1')
        except BaseException as exc:
            # The transaction rolled back, including candidate and links. Never rerun automatically.
            registry.finish(attempt['id'], state='blocked', evidence={'reason': 'result commit failed',
                            'error': str(exc), 'orphan_artifacts': refs_out})
            raise
        return report
    except BaseException as exc:
        if registry.state(run['id']) == 'running':
            registry.finish(attempt['id'], state='blocked', evidence={'reason': 'post-computation persistence interrupted',
                            'error': str(exc), 'output_directory': str(Path(options['output']).resolve())})
        raise
    finally:
        CURRENT.reset(token)


def registered_candidate(engine):
    @wraps(engine)
    def wrapper(history, **options):
        if VERIFYING.get() or CURRENT.get() is not None:
            return engine(history, **options)
        return execute(engine, history, **options)
    return wrapper


def replay(registry, run_id, output):
    run = registry.get('runs', run_id)
    return execute_spec(registry, run['spec_id'], run['document'].get('inputs', {}), output,
                        execution_key='replay:' + digest([run_id, str(Path(output).resolve())]), origin='replay')


def execute_spec(registry, spec_id, input_references, output, *, execution_key, origin='manual'):
    """Execute a declared candidate contract using trusted local, verified inputs."""
    import pickle
    from .data import DataRepository
    from .experiments import ExperimentConfig, _frame_hash, run_experiment
    spec = registry.get('specs', spec_id)
    if spec['registration_mode'] != 'preregistered':
        raise ValueError('Legacy imports do not imply reproducibility')
    contract = spec['document']['contract']
    if contract['model'].get('family') != 'nested-xgboost-v1':
        raise ValueError('Automatic replay supports the candidate workflow; ancillary procedures require their original explicit invocation')
    if contract['code'] != code_manifest():
        raise ValueError('Replay requires the registered source-code and dependency versions')
    if set(input_references) != set(contract['data']):
        raise ValueError('Input references must exactly match the registered data manifest')
    frames = {}
    for symbol, ref in input_references.items():
        if 'snapshot_id' in ref:
            frame = DataRepository(registry.store.path.parent / 'datasets').load(ref['snapshot_id'])
        else:
            content = Path(ref['path']).read_bytes()
            if sha256(content).hexdigest() != ref['sha256']:
                raise ValueError('Replay input checksum mismatch')
            frame = pickle.loads(content)
        if _frame_hash(frame) != contract['data'][symbol]['frame_sha256']:
            raise ValueError('Replay input frame mismatch')
        frames[symbol] = frame
    ticker = contract['universe'][0]
    return execute(run_experiment, frames['history'], registry=registry,
        registered_spec_id=spec_id, execution_key=execution_key, origin=origin,
        ticker=ticker, output=output,
        contexts={s.removeprefix('context:'): f for s, f in frames.items() if s.startswith('context:')},
        task=contract['task'], feature_sets=tuple(contract['features']['sets']),
        config=ExperimentConfig(**contract['evaluation']['config']),
        include_gru=contract['model']['include_gru'], time_budget=contract['stopping']['time_budget_seconds'])
