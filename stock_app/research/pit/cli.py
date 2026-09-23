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
    cmd=sub.add_parser('resolve'); cmd.add_argument('value'); cmd.add_argument('--on-date'); cmd.add_argument('--scope'); cmd.add_argument('--namespace',default='ticker')
    for name in ('security','identifiers','history'):
        sub.add_parser(name).add_argument('id')
    cmd=sub.add_parser('as-of'); cmd.add_argument('--time',required=True); cmd.add_argument('--strictness',choices=('strict','allow_proxy','observed_only'),required=True)
    cmd.add_argument('--identity-id'); cmd.add_argument('--data-type'); cmd.add_argument('--source-id')
    cmd=sub.add_parser('audit'); cmd.add_argument('--depth',choices=('metadata','artifact'),default='metadata'); cmd.add_argument('--time')
    sub.add_parser('leakage-check').add_argument('manifest',type=Path)
    a=parser.parse_args(argv); path=a.database
    if a.command=='sources': result=list_rows(path,'sources')
    elif a.command=='resolve': result=resolve_identifier(path,a.value,on_date=a.on_date,scope=a.scope,namespace=a.namespace)
    elif a.command=='security': result=get(path,'identities',a.id)
    elif a.command=='identifiers': result=[r for r in list_rows(path,'identifiers') if r['identity_id']==a.id]
    elif a.command=='history': result={'event':get(path,'events',a.id),'revisions':[r for r in list_rows(path,'revisions') if r['event_id']==a.id]}
    elif a.command=='as-of': result=get_as_of(path,as_of=a.time,strictness=a.strictness,identity_id=a.identity_id,data_type=a.data_type,source_id=a.source_id)
    elif a.command=='audit': result=audit(path,depth=a.depth,as_of=a.time)
    else: result=leakage_check(path,json.loads(a.manifest.read_text()))
    print(json.dumps(result,indent=2,allow_nan=False)); return 0
