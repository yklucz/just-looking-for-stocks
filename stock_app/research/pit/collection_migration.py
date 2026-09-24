"""Backup-first Phase 2B installation; inventory includes every Phase 2A row."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from ..forecast_identity import digest
from ..integrity_replay import exclusive
from .migration import inventory as legacy_inventory
from .collection_schema import TABLES, create_schema


def inventory(db):
    result = legacy_inventory(db)
    result['tables'] = {}
    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if r[0] not in TABLES]
    for name in sorted(names):
        quoted = '"' + name.replace('"', '""') + '"'
        content = sorted([list(r) for r in db.execute('SELECT * FROM ' + quoted)], key=lambda r: json.dumps(r, sort_keys=True))
        result['tables'][name] = {'count': len(content), 'sha256': digest(content)}
    return result


def migrate(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with exclusive(path.parent / 'worker.lock') as owned:
        if not owned:
            raise ValueError('Worker active; migration refused')
        token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid4().hex[:8]
        backup = path.with_name('research.before-provider-' + token + '.sqlite3')
        with sqlite3.connect(path) as db:
            before = inventory(db)
            with sqlite3.connect(backup) as target:
                db.backup(target)
            create_schema(db)
            after = inventory(db)
            if before != after or db.execute('PRAGMA foreign_key_check').fetchall():
                raise RuntimeError('Preservation verification failed; inspect backup')
        result = {'status': 'verified', 'backup': str(backup), 'before': before, 'after': after}
        report = path.parent / ('provider-migration-' + token + '.json')
        report.write_text(json.dumps(result, indent=2))
        return dict(result, report=str(report))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database')
    print(json.dumps(migrate(parser.parse_args().database), indent=2))
