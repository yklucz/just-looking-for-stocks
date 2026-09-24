"""Phase 0 integration. Provider refresh registration is explicitly opt-in."""
from datetime import timedelta
from types import SimpleNamespace
from ..forecast_identity import digest
from ..integrity_replay import exclusive
from ...jobs.core import JobDefinition, JobRunner, UnsafeExecution
from .collection import Collector, Scope
from .store import PITStore
from .transport import Config


def definitions():
    return [JobDefinition('pit-refresh:sec', 'pit-refresh', 'SEC'),
            JobDefinition('pit-refresh:alfred', 'pit-refresh', 'ALFRED')]


def refresh_handler(research_store, clock):
    def execute(job, context):
        config = Config.environment()
        provider = job.symbol.lower()
        config.validate(provider)
        run = context.store.get(context.run_id)
        import pandas as pd
        end = pd.Timestamp(run['scheduled_at']).date()
        start = end - timedelta(days=30)
        scope = Scope(provider, str(start), str(end), series=config.series, mode='refresh',
                      observation_start=str(start), observation_end=str(end))
        context.begin_effects({'provider_scope': scope.plan(config), 'collection_key': context.run_id})
        result = Collector(PITStore(research_store, clock=lambda: clock().isoformat()), config).collect(scope, execution_key=context.run_id)
        context.store.update(context.run_id, outputs={'collection_id': result['id']})
        if result['status'] != 'completed':
            raise UnsafeExecution('Provider collection incomplete; resume retained evidence explicitly')
        return result
    return execute


def run_operator(collector, scope, execution_key, *, resume=False):
    """Explicit operator continuation preserves the original collection identity.

    Phase 0 blocked history is never rewritten. A requested resume creates a new
    operational continuation after taking the shared worker lock.
    """
    definition = JobDefinition('pit-' + scope.mode + ':' + scope.provider + ':' + digest([scope.plan(collector.config), execution_key]),
                               'pit-collection', scope.provider)
    def execute(job, context):
        collector.config.validate(scope.provider)
        context.begin_effects({'scope': scope.plan(collector.config), 'collection_key': execution_key, 'operator_resume': resume})
        result = collector.collect(scope, execution_key=execution_key)
        context.store.update(context.run_id, outputs={'collection_id': result['id']})
        if result['status'] != 'completed':
            raise UnsafeExecution('Provider collection incomplete; inspect retained pages and explicitly resume')
        return result
    runtime = SimpleNamespace(store=collector.store)
    runner = JobRunner(runtime, [definition], {'pit-collection': execute}, clock=collector.pit.clock)
    with exclusive(collector.store.path.parent / 'worker.lock') as owned:
        if not owned:
            return {'status': 'busy'}
        prior = [r for r in runner.store.runs() if r['name'] == definition.name]
        if prior and not resume:
            return prior[-1]
        run = runner.enqueue(definition, runner.now())
        return runner._execute(run['id'])
