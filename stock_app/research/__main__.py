"""Offline-capable local research administration: python -m stock_app.research."""
import argparse
import json
from pathlib import Path

from .runtime import get_runtime


def replay_experiment(runtime, job_id, output):
    """Reproduce a recorded experiment from its exact verified snapshots offline."""
    from .experiments import ExperimentConfig, run_experiment
    job = runtime.store.get('jobs', job_id)
    if job['kind'] != 'experiment' or job['state'] != 'completed' or not job.get('snapshots'):
        raise ValueError('Replay requires a completed experiment with pinned snapshots')
    frames = {symbol: runtime.data.load(identity) for symbol, identity in job['snapshots'].items()}
    report = job['progress']
    inputs = report['fingerprints']['inputs']
    with runtime.worker_lease() as owned:
        if not owned:
            raise ValueError('A research worker is already running; retry after it finishes')
        return run_experiment(frames[job['symbol']], ticker=job['symbol'], output=output,
                              contexts=frames, feature_sets=tuple(inputs['feature_sets']),
                              task=job['parameters']['task'], config=ExperimentConfig(**report['config']),
                              include_gru=inputs['include_gru'])


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'data':
        from .pit.cli import main as data_main
        return data_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == 'integrity':
        from .integrity_cli import main as integrity_main
        return integrity_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == 'registry':
        from .registry_cli import main as registry_main
        return registry_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('forecast-audit', 'migrate-forecasts', 'forecast-revisions', 'forecast-legacy', 'forecast-replay'):
        command = sub.add_parser(name)
        command.add_argument('--database', type=Path)
        if name in {'forecast-revisions', 'forecast-replay'}:
            command.add_argument('issuance_id')
        if name == 'forecast-replay':
            command.add_argument('revision_id')
    for name in ('refresh', 'experiment', 'forecast', 'company-context'):
        command = sub.add_parser(name)
        command.add_argument('--symbol', required=True)
        if name == 'experiment':
            command.add_argument('--task', choices=['binary', 'regression'], default='binary')
            command.add_argument('--include-gru', action='store_true')
    command = sub.add_parser('resume')
    command.add_argument('job_id')
    command = sub.add_parser('replay')
    command.add_argument('job_id')
    command.add_argument('--output', type=Path, required=True)
    command = sub.add_parser('backup')
    command.add_argument('destination', type=Path)
    command = sub.add_parser('export')
    command.add_argument('--kind', choices=['datasets', 'jobs', 'models', 'forecasts', 'events'], required=True)
    command.add_argument('--symbol')
    command = sub.add_parser('universe')
    command.add_argument('symbols', nargs='+')
    args = parser.parse_args()
    if args.command.startswith('forecast-') or args.command == 'migrate-forecasts':
        from .forecast_admin import run
        result = run(args)
        print(json.dumps(result, indent=2, allow_nan=False))
        return int(result.get('status') in {'invalid', 'migration_required', 'reconciliation_required', 'busy'}
                   or result.get('matches') is False)
    runtime = get_runtime()
    if args.command == 'backup':
        result = {'backup': str(runtime.store.backup(args.destination))}
    elif args.command == 'replay':
        result = replay_experiment(runtime, args.job_id, args.output)
    elif args.command == 'export':
        if args.kind == 'forecasts':
            from .forecast_store import list_issuances
            with runtime.store.connection() as db:
                result = list_issuances(db, symbol=args.symbol)
        else:
            result = runtime.store.list(args.kind, symbol=args.symbol)
    elif args.command == 'universe':
        from .config import SYMBOLS
        symbols = list(dict.fromkeys(symbol.upper() for symbol in args.symbols))
        if any(symbol not in SYMBOLS for symbol in symbols):
            parser.error('Symbols must be members of the configured universe')
        result = runtime.store.put('settings', {'id': 'universe', 'symbols': symbols})
    else:
        if args.command == 'resume':
            job = runtime.control(args.job_id, 'resume')
        else:
            job = runtime.submit(args.command.replace('-', '_'), args.symbol, getattr(args, 'task', 'binary'),
                                 include_gru=getattr(args, 'include_gru', False))
        result = runtime.run_job(job['id'])
    print(json.dumps(result, indent=2))
    return 1 if isinstance(result, dict) and result.get('state') == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
