"""Read-only JSON PIT inspection. Ingestion uses internal typed application services."""
import argparse
import json
import os
from pathlib import Path
from .store import list_rows,get,resolve_identifier
from .query import get_as_of,leakage_check
from .audit import audit


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    root=Path(os.environ.get('STOCK_RESEARCH_ROOT',Path(__file__).resolve().parents[3]/'artifacts/research'))
    parser.add_argument('--database',type=Path,default=root/'research.sqlite3')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('sources')
    sub.add_parser('provider-status')
    sub.add_parser('coverage')
    sub.add_parser('migrate-providers')
    cmd=sub.add_parser('raw'); cmd.add_argument('action',choices=('show','verify','reparse')); cmd.add_argument('id')
    for name in ('collect','backfill'):
        cmd=sub.add_parser(name); cmd.add_argument('provider',choices=('sec','alfred'))
        cmd.add_argument('--start',required=True); cmd.add_argument('--end',required=True)
        cmd.add_argument('--series'); cmd.add_argument('--observation-start',default=''); cmd.add_argument('--observation-end',default='')
        cmd.add_argument('--page-size',type=int,default=100); cmd.add_argument('--max-pages',type=int,default=12)
        cmd.add_argument('--execution-key'); cmd.add_argument('--dry-run',action='store_true'); cmd.add_argument('--resume',action='store_true')
    cmd=sub.add_parser('resolve'); cmd.add_argument('value'); cmd.add_argument('--on-date'); cmd.add_argument('--scope'); cmd.add_argument('--namespace',default='ticker')
    for name in ('security','identifiers','history'):
        sub.add_parser(name).add_argument('id')
    cmd=sub.add_parser('as-of'); cmd.add_argument('--time',required=True); cmd.add_argument('--strictness',choices=('strict','allow_proxy','observed_only'),required=True)
    cmd.add_argument('--identity-id'); cmd.add_argument('--data-type'); cmd.add_argument('--source-id')
    cmd=sub.add_parser('audit'); cmd.add_argument('--depth',choices=('metadata','artifact'),default='metadata'); cmd.add_argument('--time')
    sub.add_parser('leakage-check').add_argument('manifest',type=Path)
    a=parser.parse_args(argv); path=a.database
    if a.command in {'collect','backfill'}:
        from .collection import Collector,Scope
        from .transport import Config
        config=Config.environment()
        scope=Scope(a.provider,a.start,a.end,series=tuple(a.series.split(',')) if a.series else config.series,
                    observation_start=a.observation_start,observation_end=a.observation_end,
                    page_size=a.page_size,max_pages=a.max_pages,mode='refresh' if a.command=='collect' else 'backfill')
        if a.dry_run:
            result=scope.plan(config)
        else:
            if not a.execution_key: parser.error('--execution-key required outside dry-run')
            config.validate(a.provider)
            from ..store import ResearchStore
            from .store import PITStore
            from .collection_jobs import run_operator
            result=run_operator(Collector(PITStore(ResearchStore(path)),config),scope,a.execution_key,resume=a.resume)
    elif a.command=='migrate-providers':
        from .collection_migration import migrate
        result=migrate(path)
    elif a.command in {'provider-status','coverage'}:
        from .collection import status,coverage
        result=(status if a.command=='provider-status' else coverage)(path)
    elif a.command=='raw':
        from .collection import verify_raw,Collector
        if a.action=='show': result=get(path,'raw',a.id)
        elif a.action=='verify': result=verify_raw(path,a.id)
        else:
            from ..store import ResearchStore
            from .store import PITStore
            from ..integrity_replay import exclusive
            with exclusive(path.parent/'worker.lock') as owned:
                if not owned: raise ValueError('Worker active; reparse refused')
                result=Collector(PITStore(ResearchStore(path))).reparse(a.id)
    elif a.command=='sources': result=list_rows(path,'sources')
    elif a.command=='resolve': result=resolve_identifier(path,a.value,on_date=a.on_date,scope=a.scope,namespace=a.namespace)
    elif a.command=='security': result=get(path,'identities',a.id)
    elif a.command=='identifiers': result=[r for r in list_rows(path,'identifiers') if r['identity_id']==a.id]
    elif a.command=='history': result={'event':get(path,'events',a.id),'revisions':[r for r in list_rows(path,'revisions') if r['event_id']==a.id]}
    elif a.command=='as-of': result=get_as_of(path,as_of=a.time,strictness=a.strictness,identity_id=a.identity_id,data_type=a.data_type,source_id=a.source_id)
    elif a.command=='audit': result=audit(path,depth=a.depth,as_of=a.time)
    else: result=leakage_check(path,json.loads(a.manifest.read_text()))
    print(json.dumps(result,indent=2,allow_nan=False)); return int(isinstance(result,dict) and result.get('status') in {'blocked','failed','busy','retry'})
