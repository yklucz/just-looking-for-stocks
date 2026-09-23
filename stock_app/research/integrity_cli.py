"""JSON-only local integrity inspection and explicitly authorized operator actions."""
import argparse
import json
import os
from pathlib import Path

from .integrity import get_evidence, verify_run
from .integrity_audit import audit_integrity
from .integrity_reconcile import detect_cases, list_cases, resolve
from .integrity_replay import comparison_report, reproduction_history, request_reproduction
from .store import ResearchStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(os.environ.get('STOCK_RESEARCH_ROOT', Path(__file__).resolve().parents[2] / 'artifacts/research'))
    parser.add_argument('--database', type=Path, default=root/'research.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('evidence','verify','replay','history','compare'):
        cmd = sub.add_parser(name)
        cmd.add_argument('run_id')
        if name == 'verify':
            cmd.add_argument('--depth', choices=('metadata','artifact'), default='artifact')
        if name == 'replay':
            cmd.add_argument('--request-key', required=True)
        if name == 'compare':
            cmd.add_argument('reproduction_id')
    cmd = sub.add_parser('audit')
    cmd.add_argument('--depth', choices=('metadata','artifact'), default='metadata')
    cmd.add_argument('--replay', action='store_true', help='Explicitly persist verification checks for every run')
    cmd.add_argument('--request-key', help='Required request batch identity with --replay')
    cmd = sub.add_parser('reconcile')
    actions = cmd.add_subparsers(dest='action', required=True)
    actions.add_parser('list')
    actions.add_parser('detect')
    for name in ('show','resolve'):
        action = actions.add_parser(name)
        action.add_argument('case_id')
        if name == 'resolve':
            action.add_argument('--action', dest='resolution', required=True)
            action.add_argument('--actor', required=True)
            action.add_argument('--reason', required=True)
            action.add_argument('--evidence', type=Path, help='JSON object supporting the operator decision')
    args = parser.parse_args(argv)
    path = args.database
    if args.command == 'evidence':
        result = get_evidence(path, args.run_id)
    elif args.command == 'verify':
        result = verify_run(path, args.run_id, args.depth)
    elif args.command == 'replay':
        result = request_reproduction(ResearchStore(path), args.run_id, request_key=args.request_key)
    elif args.command == 'history':
        result = reproduction_history(path, args.run_id)
    elif args.command == 'compare':
        result = comparison_report(path, args.run_id, args.reproduction_id)
    elif args.command == 'audit':
        if args.replay and not args.request_key:
            parser.error('--replay requires --request-key')
        result = audit_integrity(path, depth=args.depth)
        if args.replay:
            store = ResearchStore(path)
            result['reproduction_checks'] = [request_reproduction(store, r['run_id'], request_key=args.request_key+':'+r['run_id']) for r in result.get('reviews', [])]
            result['replay_performed'] = True
    elif args.action in {'list','show'}:
        result = list_cases(path, getattr(args, 'case_id', None))
    elif args.action == 'detect':
        result = detect_cases(ResearchStore(path))
    else:
        evidence = json.loads(args.evidence.read_text()) if args.evidence else None
        if evidence is not None and not isinstance(evidence, dict):
            parser.error('--evidence must contain an object')
        result = resolve(ResearchStore(path), args.case_id, action=args.resolution, actor=args.actor, reason=args.reason, evidence=evidence)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
