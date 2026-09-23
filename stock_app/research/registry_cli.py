"""Local typed registry administration; inspection never starts a worker."""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from .registry import Registry, TABLES
from .registry_admin import audit, import_legacy
from .store import ResearchStore


def read_rows(path, kind='runs', identity=None):
    if kind not in TABLES:
        raise ValueError('Unknown registry entity')
    if not Path(path).exists():
        if identity:
            raise KeyError(identity)
        return []
    with sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", ('research_' + kind,)).fetchone():
            if identity:
                raise KeyError(identity)
            return []
        query = f'SELECT * FROM research_{kind}'
        args = ()
        if identity:
            query += ' WHERE id=?'
            args = (identity,)
        rows = [dict(r) for r in db.execute(query + ' ORDER BY created_at,rowid', args)]
        for row in rows:
            if 'document' in row:
                row['document'] = json.loads(row['document'])
            if kind == 'runs':
                row['registration_mode'] = db.execute('SELECT registration_mode FROM research_specs WHERE id=?', (row['spec_id'],)).fetchone()[0]
                attempts = [dict(a) for a in db.execute('SELECT * FROM research_attempts WHERE run_id=? ORDER BY number', (row['id'],))]
                outcomes = [dict(o) for o in db.execute('SELECT * FROM research_outcomes WHERE run_id=? ORDER BY created_at,rowid', (row['id'],))]
                for child in attempts + outcomes:
                    child['document'] = json.loads(child['document'])
                row.update(attempts=attempts, outcomes=outcomes)
                latest = next((o for o in outcomes if attempts and o['attempt_id'] == attempts[-1]['id']), None)
                row['state'] = latest['state'] if latest else 'running' if attempts else 'registered'
    if identity:
        if not rows:
            raise KeyError(identity)
        return rows[0]
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(os.environ.get('STOCK_RESEARCH_ROOT', Path(__file__).resolve().parents[2] / 'artifacts/research'))
    parser.add_argument('--database', type=Path, default=root / 'research.sqlite3')
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('list', 'show', 'history', 'audit'):
        cmd = sub.add_parser(name)
        if name in ('list', 'show'):
            cmd.add_argument('--kind', choices=TABLES, default='runs')
        if name == 'audit':
            cmd.add_argument('--integrity', action='store_true')
            cmd.add_argument('--depth', choices=('metadata','artifact'), default='metadata')
        if name == 'show':
            cmd.add_argument('id')
    for name in ('question', 'hypothesis', 'family', 'spec', 'trial'):
        cmd = sub.add_parser('register-' + name)
        cmd.add_argument('json_file', type=Path)
    cmd = sub.add_parser('import-legacy')
    cmd.add_argument('--apply', action='store_true')
    cmd = sub.add_parser('replay')
    cmd.add_argument('id')
    cmd.add_argument('--output', type=Path, required=True)
    cmd = sub.add_parser('execute')
    cmd.add_argument('spec_id')
    inputs = cmd.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--inputs-from', help='Run containing the frozen input references')
    inputs.add_argument('--inputs', type=Path, help='JSON mapping of verified local input references')
    cmd.add_argument('--execution-key', required=True)
    cmd.add_argument('--output', type=Path, required=True)
    cmd = sub.add_parser('reconcile')
    cmd.add_argument('id')
    cmd.add_argument('--reason', required=True)
    cmd.add_argument('--actor', required=True)
    args = parser.parse_args(argv)
    if args.action == 'audit':
        if args.integrity:
            from .integrity_audit import audit_integrity
            result = audit_integrity(args.database, depth=args.depth)
        else:
            result = audit(args.database)
    elif args.action in ('list', 'show', 'history'):
        result = read_rows(args.database, getattr(args, 'kind', 'runs'), getattr(args, 'id', None))
    else:
        registry = Registry(ResearchStore(args.database))
        if args.action.startswith('register-'):
            result = getattr(registry, args.action.removeprefix('register-'))(**json.loads(args.json_file.read_text()))
        elif args.action == 'import-legacy':
            result = import_legacy(registry, apply=args.apply)
        elif args.action == 'reconcile':
            result = registry.reconcile(args.id, reason=args.reason, actor=args.actor)
        else:
            from .registry_execution import replay
            if args.action == 'execute':
                from .registry_execution import execute_spec
                refs = registry.get('runs', args.inputs_from)['document']['inputs'] if args.inputs_from else json.loads(args.inputs.read_text())
                result = execute_spec(registry, args.spec_id, refs, args.output, execution_key=args.execution_key)
            else:
                result = replay(registry, args.id, args.output)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
