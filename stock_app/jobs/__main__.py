"""Run with python -m stock_app.jobs; configure STOCK_RESEARCH_ROOT as for research."""
import argparse
import json
import logging

from .service import JobService


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('run-due')
    command = sub.add_parser('run')
    command.add_argument('job_name')
    command.add_argument('--scheduled-at', help='Exact due UTC slot from status; default is latest slot')
    sub.add_parser('status')
    sub.add_parser('health')
    command = sub.add_parser('reconcile', help='Record manual inspection without repeating effects')
    command.add_argument('run_id')
    command.add_argument('--outcome', required=True, choices=['succeeded', 'cancelled'])
    command.add_argument('--note', required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    service = JobService()
    if args.command == 'status':
        result = service.status()
    elif args.command == 'health':
        result = service.health()
    elif args.command == 'reconcile':
        try:
            result = service.runner.reconcile(args.run_id, outcome=args.outcome, note=args.note)
        except (KeyError, ValueError) as error:
            parser.error(str(error))
    else:
        try:
            result = service.runner.run_due(name=getattr(args, 'job_name', None),
                                            scheduled_at=getattr(args, 'scheduled_at', None))
        except ValueError as error:
            parser.error(str(error))
    print(json.dumps(result, indent=2))
    if args.command == 'health':
        return 0 if result['status'] == 'healthy' else 1
    if result.get('status') == 'busy':
        return 2
    if args.command in {'run', 'run-due'}:
        # Include unresolved earlier failures, even if this invocation did no work.
        return int(any(run['status'] in {'retry', 'failed', 'blocked'}
                       for run in service.runner.store.runs()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
