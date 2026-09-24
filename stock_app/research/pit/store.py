"""Typed immutable source evidence and permanent internal identities."""
import json

from ..forecast_identity import canonical_json, digest
from ..integrity import reader, rows
from ..registry_execution import immutable_bytes
from ..store import utcnow
from .schema import TABLES
from .temporal import Availability, instant, day, temporal_value, bound


def list_rows(path,kind):
    if 'pit_'+kind not in TABLES:
        raise ValueError('Unknown PIT entity')
    with reader(path) as db:
        return rows(db,'pit_'+kind)


def get(path,kind,identity):
    if 'pit_'+kind not in TABLES:
        raise ValueError('Unknown PIT entity')
    with reader(path) as db:
        row=db.execute('SELECT * FROM pit_'+kind+' WHERE id=?',(identity,)).fetchone()
    if row is None:
        raise KeyError(identity)
    value=dict(row)
    value['document']=json.loads(value['document'])
    return value


class PITStore:
    def __init__(self,store,clock=utcnow):
        self.store,self.clock=store,clock

    def _insert(self,kind,identity,doc,columns):
        fingerprint=digest(doc)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT fingerprint FROM pit_'+kind+' WHERE id=?',(identity,)).fetchone()
            if prior:
                if prior[0]!=fingerprint:
                    raise ValueError('Permanent identity conflicts with recorded evidence')
            else:
                values={'id':identity,**columns,'fingerprint':fingerprint,'document':canonical_json(doc)}
                db.execute('INSERT INTO pit_'+kind+' ('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',list(values.values()))
        return get(self.store.path,kind,identity)

    def source(self,*,provider,feed,source_type,schema_version,temporal_capabilities):
        if any(not isinstance(v,str) or not v.strip() for v in (provider,feed,source_type,schema_version)):
            raise ValueError('Source identity fields required')
        doc=dict(provider=provider,feed=feed,source_type=source_type,schema_version=schema_version,temporal_capabilities=temporal_capabilities)
        return self._insert('sources','src-'+digest([provider,feed]),doc,{'provider':provider,'feed':feed,'created_at':instant(self.clock())})

    def identity(self,*,kind,key,name,evidence,parent_id=None,exchange=None):
        if kind not in {'entity','security','listing'} or not key or not name or not evidence:
            raise ValueError('Explicit internal key, name, kind and identity evidence required')
        if kind=='listing' and not exchange:
            raise ValueError('Listing needs exchange identity')
        doc=dict(kind=kind,key=key,name=name,evidence=evidence,parent_id=parent_id,exchange=exchange)
        return self._insert('identities',kind+'-'+digest([kind,key]),doc,{'kind':kind,'parent_id':parent_id,'created_at':instant(self.clock())})

    def identifier(self,*,identity_id,source_id,namespace,value,evidence,scope='',valid_from=None,valid_to=None,raw_id=None,open_ended=False):
        if namespace not in {'ticker','cik','isin','cusip','provider_entity','provider_security','other'} or not value or not evidence:
            raise ValueError('Typed namespace, value and mapping evidence required')
        if valid_from is not None: valid_from=day(valid_from)
        if valid_to is not None: valid_to=day(valid_to)
        if type(open_ended) is not bool or (open_ended and (valid_from is None or valid_to is not None)):
            raise ValueError('Open-ended validity must be explicitly declared with a known start and no end')
        if valid_from and valid_to and valid_from>=valid_to:
            raise ValueError('Identifier interval is half-open and must be positive')
        if namespace=='ticker': value=value.strip().upper()
        if namespace=='cik':
            if not value.isdigit() or len(value)>10: raise ValueError('CIK must contain at most ten digits')
            value=value.zfill(10)
        doc=dict(identity_id=identity_id,source_id=source_id,namespace=namespace,value=value,scope=scope,
                 valid_from=valid_from,valid_to=valid_to,evidence=evidence,raw_id=raw_id,open_ended=open_ended)
        return self._insert('identifiers','alias-'+digest(doc),doc,{k:doc[k] for k in ('identity_id','source_id','namespace','value','scope','valid_from','valid_to','raw_id')} | {'created_at':instant(self.clock())})

    def raw(self,*,source_id,resource,payload,observed_time,parser_version,metadata=None):
        get(self.store.path,'sources',source_id)
        observed=instant(observed_time); ingested=instant(self.clock())
        if observed>ingested or not resource or not parser_version or not isinstance(payload,bytes):
            raise ValueError('Raw evidence needs bytes, resource/parser and observed <= ingested')
        ref=immutable_bytes(self.store.path.parent/'pit-raw',payload,'.bin')
        doc=dict(source_id=source_id,resource=resource,sha256=ref['sha256'],path=ref['path'],
                 observed_time=observed,parser_version=parser_version,metadata=metadata or {})
        return self._insert('raw','raw-'+digest(doc),doc,{'source_id':source_id,'sha256':ref['sha256'],'observed_time':observed,'ingested_time':ingested})

    def event(self,*,source_id,source_key,data_type,identity_id=None):
        if not source_key or not data_type:
            raise ValueError('Logical provider key and data type required')
        doc=dict(source_id=source_id,source_key=source_key,data_type=data_type,identity_id=identity_id)
        return self._insert('events','evt-'+digest([source_id,data_type,source_key]),doc,{**doc,'created_at':instant(self.clock())})

    def revision(self,*,event_id,raw_id,source_record_id,payload,availability,event_time=None,event_end=None,
                 source_time=None,supersedes=None,parser_version='pit-v1',data_vintage='historical',relations=None):
        event=get(self.store.path,'events',event_id); raw=get(self.store.path,'raw',raw_id)
        if event['source_id']!=raw['source_id'] or not source_record_id:
            raise ValueError('Revision/raw source identity mismatch')
        if data_vintage not in {'historical','current_vintage'}:
            raise ValueError('Explicit data vintage required')
        if not isinstance(availability,Availability):
            raise ValueError('Typed Availability required')
        a=availability.document()
        if data_vintage=='current_vintage' and a['kind'] not in {'observed_only','unknown'}:
            raise ValueError('Current-vintage data cannot claim historical availability')
        observed=raw['observed_time']; ingested=instant(self.clock())
        if observed>ingested: raise ValueError('Observation cannot follow ingestion')
        et,ee,st=map(temporal_value,(event_time,event_end,source_time))
        if et and ee and (et['precision']!=ee['precision'] or et['value']>ee['value']):
            raise ValueError('Invalid event range')
        if st and st['precision']=='instant' and st['value']>observed:
            raise ValueError('Source timestamp follows observation')
        available=bound(a,observed)
        if a['kind'] in {'exact','proxy'} and available>observed:
            raise ValueError('Declared availability follows observation')
        doc=dict(event_id=event_id,source_id=event['source_id'],raw_id=raw_id,source_record_id=source_record_id,
                 event_time=et,event_end=ee,source_time=st,availability=a,observed_time=observed,
                 payload=payload,payload_fingerprint=digest(payload),supersedes=supersedes,
                 parser_version=parser_version,data_vintage=data_vintage,relations=relations or {})
        return self._insert('revisions','rev-'+digest(doc),doc,{'event_id':event_id,'source_id':event['source_id'],
            'raw_id':raw_id,'source_record_id':source_record_id,'supersedes':supersedes,'availability_kind':a['kind'],
            'available_bound':available,'observed_time':observed,'ingested_time':ingested})


def resolve_identifier(path,value,*,namespace='ticker',scope=None,on_date=None):
    value=value.strip().upper() if namespace=='ticker' else value.zfill(10) if namespace=='cik' else value
    when=day(on_date) if on_date else None
    with reader(path) as db:
        query='SELECT * FROM pit_identifiers WHERE namespace=? AND value=?'
        args=[namespace,value]
        if scope is not None: query+=' AND scope=?'; args.append(scope)
        matches=[dict(r) for r in db.execute(query,args)]
    for row in matches: row['document']=json.loads(row['document'])
    known=[]; uncertain=[]
    for row in matches:
        if when:
            if row['valid_from'] is None:
                if row['valid_to'] and when>=row['valid_to']: continue
                uncertain.append(row); continue
            if row['valid_from']>when or (row['valid_to'] and when>=row['valid_to']): continue
            if row['valid_to'] is None and not row['document'].get('open_ended',False):
                uncertain.append(row); continue
        known.append(row)
    identities={r['identity_id'] for r in known}
    status='resolved' if len(identities)==1 and not uncertain else 'ambiguous' if len(identities)>1 or uncertain else 'legacy_symbol' if namespace=='ticker' else 'unknown'
    return {'status':status,'value':value,'namespace':namespace,'on_date':when,'identity_id':next(iter(identities)) if status=='resolved' else None,
            'matches':known,'unknown_period_matches':uncertain,'legacy_symbol':value if status=='legacy_symbol' else None,
            'historical_period_proven':bool(when and status=='resolved')}
