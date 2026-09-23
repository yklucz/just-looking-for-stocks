"""Backup-first installation that inventories all earlier phases, including integrity."""
from datetime import datetime,timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from ..forecast_identity import digest
from ..integrity_replay import exclusive
from .schema import TABLES,create_schema


def inventory(db):
    result={}
    names=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if r[0] not in TABLES]
    for name in sorted(names):
        quoted='"'+name.replace('"','""')+'"'
        content=sorted([list(r) for r in db.execute('SELECT * FROM '+quoted)],key=lambda r:json.dumps(r,sort_keys=True))
        result[name]={'count':len(content),'sha256':digest(content)}
    models={}
    for identity,document in db.execute("SELECT id,document FROM records WHERE kind='models'"):
        file=Path(json.loads(document)['artifact'])
        models[identity]={'path':str(file),'sha256':sha256(file.read_bytes()).hexdigest() if file.is_file() else None}
    return {'tables':result,'models':models}


def migrate(path):
    path=Path(path).resolve()
    if not path.is_file(): raise FileNotFoundError(path)
    with exclusive(path.parent/'worker.lock') as owned:
        if not owned: raise ValueError('Worker active; migration refused')
        token=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:8]
        backup=path.with_name('research.before-pit-'+token+'.sqlite3')
        with sqlite3.connect(path) as db:
            before=inventory(db)
            with sqlite3.connect(backup) as target: db.backup(target)
            db.execute('PRAGMA foreign_keys=ON'); create_schema(db)
            after=inventory(db)
            if before!=after: raise RuntimeError('Original inventory changed; inspect backup')
            if db.execute('PRAGMA foreign_key_check').fetchall(): raise RuntimeError('Foreign key violation')
        report=path.parent/('pit-migration-verification-'+token+'.json')
        result={'status':'verified','backup':str(backup),'before':before,'after':after,'report':str(report),'additive_tables':list(TABLES)}
        report.write_text(json.dumps(result,indent=2))
        return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('database')
    print(json.dumps(migrate(parser.parse_args().database),indent=2))
