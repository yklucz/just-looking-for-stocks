"""Preregister ancillary public research entry points, including exploratory addenda.

No execution caching: each explicit ancillary invocation is a new execution, and
reuses a content-addressed specification. Candidate checkpoint retries use the
stricter intended-execution semantics in registry_execution instead.
"""
from dataclasses import asdict, is_dataclass
from functools import wraps
from hashlib import sha256
import inspect
import json
from pathlib import Path
import pickle
from uuid import uuid4

import numpy as np
import pandas as pd

from .forecast_identity import canonical_json, digest
from .registry_execution import CURRENT, VERIFYING, code_manifest, default_registry, immutable_bytes


def stable(value):
    if is_dataclass(value):
        return stable(asdict(value))
    if isinstance(value, dict):
        return {str(k): stable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [stable(v) for v in value]
    if isinstance(value, (pd.Index, np.ndarray, pd.Series)):
        return {'values': [stable(v) for v in value.tolist()]}
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        if value.is_file():
            return {'file_sha256': sha256(value.read_bytes()).hexdigest()}
        return {'unavailable_reference': value.name}
    if callable(value):
        try:
            source = inspect.getsource(value)
        except (TypeError, OSError):
            source = None
        closure = getattr(value, '__closure__', None)
        return {'callable': value.__module__ + '.' + value.__qualname__,
                'source_sha256': sha256(source.encode()).hexdigest() if source else None,
                'defaults': stable(getattr(value, '__defaults__', None)),
                'closure': [stable(c.cell_contents) for c in closure] if closure else [],
                'replay_limit': 'External globals and native library state are not captured'}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {'type': type(value).__module__ + '.' + type(value).__qualname__, 'opaque': True}


def registered_workflow(engine):
    @wraps(engine)
    def wrapper(*args, **kwargs):
        if VERIFYING.get() or CURRENT.get() is not None:
            return engine(*args, **kwargs)
        from .experiments import _frame_hash
        from ..config import DEFAULT_SPLIT, TargetConfig, GRUConfig, FeatureConfig
        bound = inspect.signature(engine).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        history = arguments['history']
        registry = default_registry()
        family_name = engine.__name__
        exploratory = family_name == 'enrich_feature_comparison'
        frames = {'history': history, **(arguments.get('contexts') or {}), **(arguments.get('frames') or {})}
        data, references = {}, {}
        for name, frame in frames.items():
            data[name] = {'frame_sha256': _frame_hash(frame), 'rows': len(frame)}
            references[name] = immutable_bytes(registry.store.path.parent / 'registry-inputs', pickle.dumps(frame, protocol=5), '.pickle')
        parameters = {k: stable(v) for k, v in arguments.items()
                      if k not in {'history', 'contexts', 'frames', 'output', 'checkpoints', 'cancelled', 'source'}}
        task, ticker = 'binary', arguments.get('ticker', 'supplied history')
        target = arguments.get('target_config', TargetConfig(task='binary'))
        feature_contract = stable(arguments.get('feature_config', {'source': 'declared frame inputs'}))
        metric = 'log_loss'
        if family_name == 'run_gru_challenger':
            cfg = arguments['config']
            target = TargetConfig(task='binary', horizon=cfg.horizon, threshold=cfg.event_threshold)
            parameters['gru_config'] = stable(arguments.get('gru_config') or GRUConfig(device='cpu'))
            metric = 'brier'
        if exploratory:
            source_path = Path(arguments['output']) / 'report.json'
            source_bytes = source_path.read_bytes()
            source = json.loads(source_bytes)
            parameters['source_report_sha256'] = sha256(source_bytes).hexdigest()
            parameters['source_configuration'] = source['config']
            task, ticker = source['task'], source['ticker']
            target = TargetConfig(task=task, horizon=source['config']['horizon'], threshold=source['config']['event_threshold'])
            feature_contract = {'sets': source['fingerprints']['inputs']['feature_sets'], 'config': asdict(FeatureConfig())}
            metric = 'brier' if task == 'binary' else 'mae'
        question = registry.question(title='Existing ' + family_name + ' research procedure',
            description='Record the explicitly invoked research procedure and its complete evaluation evidence.', creator='existing research CLI')
        hypothesis = registry.hypothesis(question_id=question['id'],
            statement=f'The declared procedure improves held-out {metric} relative to its causal baseline comparators.',
            effect='Lower held-out primary loss.', rationale='Evaluate the existing configured model comparison.',
            falsification='No held-out loss reduction against the baseline falsifies the expected direction.')
        family = registry.family(hypothesis_id=hypothesis['id'], dimensions={'configuration': [parameters]})
        trial = registry.trial(family_id=family['id'], parameters={'configuration': parameters})
        contract = {'task': task, 'universe': [ticker],
            'data': data, 'evaluation': {'procedure': family_name, 'parameters': parameters,
                                       'default_split': asdict(DEFAULT_SPLIT), 'analysis_kind': 'exploratory' if exploratory else 'declared'},
            'features': feature_contract,
            'target': stable(target), 'model': {'procedure': family_name, 'parameters': parameters},
            'primary_metric': metric, 'secondary_metrics': [],
            'baselines': ['existing procedure comparators, fixed by source fingerprint'],
            'randomness': {'policy': 'existing config seeds', 'parameters': parameters},
            'stopping': {'policy': 'existing runner completion, exception or operator cancellation'},
            'criteria': {'rule': 'evidence-only-v1', 'disposition': 'inconclusive; no automated cross-model outcome adjudication'},
            'code': code_manifest()}
        spec = registry.spec(hypothesis_id=hypothesis['id'], family_id=family['id'], contract=contract)
        run = registry.run(spec_id=spec['id'], execution_key='invocation:' + uuid4().hex,
                           origin='exploratory' if exploratory else 'manual', provenance={'inputs': references, 'procedure': family_name})
        attempt = registry.begin(run['id'])
        token = CURRENT.set((registry, run, attempt, {}))
        event = registry.event('fit_started', {'procedure': family_name}, run_id=run['id'], attempt_id=attempt['id'], trial_id=trial['id'])
        try:
            try:
                report = engine(*args, **kwargs)
            except BaseException as exc:
                state = 'aborted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'invalid' if isinstance(exc, ValueError) else 'failed'
                registry.event('fit_failed', {'start_event': event, 'error': str(exc)}, run_id=run['id'], attempt_id=attempt['id'], trial_id=trial['id'])
                registry.finish(attempt['id'], state=state, evidence={'error_type': type(exc).__name__, 'error': str(exc)})
                raise
            ref = immutable_bytes(registry.store.path.parent / 'registry-results', canonical_json(report).encode(), '.json')
            state = 'aborted' if report.get('status') in {'paused', 'cancelled'} else 'completed'
            registry.event('fit_completed', {'start_event': event, 'report': ref}, run_id=run['id'], attempt_id=attempt['id'], trial_id=trial['id'])
            registry.finish(attempt['id'], state=state,
                            metrics={metric: report['aggregate'][metric]} if isinstance(report.get('aggregate', {}).get(metric), (float, int)) else {},
                            evidence={'report': ref, 'analysis_kind': 'exploratory' if exploratory else 'declared',
                            'metric_location': 'per-model/per-fold values in immutable report; no fabricated aggregate'},
                            artifacts=[{'role': 'report', **ref}], rule='evidence-only-v1')
            return report
        except BaseException as exc:
            if registry.state(run['id']) == 'running':
                registry.finish(attempt['id'], state='blocked', evidence={'error': str(exc), 'reason': 'artifact or result persistence failed'})
            raise
        finally:
            CURRENT.reset(token)
    return wrapper


def observed_fit(engine):
    """Retain every ancillary model-fit failure, even before a report exists."""
    @wraps(engine)
    def wrapper(model, dataset):
        context = CURRENT.get()
        if context is None:
            return engine(model, dataset)
        registry, run, attempt, _ = context
        details = {'model_type': getattr(model, 'model_type', type(model).__name__),
                   'configuration': stable(getattr(model, 'config', None))}
        event = registry.event('model_fit_started', details, run_id=run['id'], attempt_id=attempt['id'])
        try:
            result = engine(model, dataset)
        except BaseException as exc:
            registry.event('model_fit_failed', {**details, 'start_event': event, 'error': str(exc)},
                           run_id=run['id'], attempt_id=attempt['id'])
            raise
        registry.event('model_fit_completed', {**details, 'start_event': event}, run_id=run['id'], attempt_id=attempt['id'])
        return result
    return wrapper
