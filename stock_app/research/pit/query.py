"""Explicit as-of selection and frozen query provenance. No latest-value API."""
from ..forecast_identity import digest
from .store import get
from ..integrity import reader
import json
from .temporal import Strictness, instant, eligible


def get_as_of(path,*,as_of,strictness,identity_id=None,data_type=None,source_id=None):
    cutoff=instant(as_of); mode=Strictness(strictness)
    filters=[]; args=[]
    for column,value in (('identity_id',identity_id),('data_type',data_type),('source_id',source_id)):
        if value is not None:
            filters.append(column+'=?'); args.append(value)
    with reader(path) as db:
        events=[dict(r) for r in db.execute('SELECT * FROM pit_events'+(' WHERE '+' AND '.join(filters) if filters else ''),args)]
        revisions=[]
        for event in events:
            for row in db.execute('SELECT * FROM pit_revisions WHERE event_id=?',(event['id'],)):
                revision=dict(row); revision['document']=json.loads(revision['document']); revisions.append(revision)
    grouped={}
    for revision in revisions: grouped.setdefault(revision['event_id'],[]).append(revision)
    selected=[]; excluded=[]; ambiguous=[]
    for event in events:
        candidates=[]
        for r in grouped.get(event['id'],[]):
            if eligible(r,cutoff,mode): candidates.append(r)
            else: excluded.append({'revision_id':r['id'],'reason':'Availability/cutoff/policy excludes this revision'})
        if not candidates: continue
        # Observation is an eligibility gate, not a license for a later download
        # of an old vintage to overwrite a newer source vintage.
        field='available_bound'
        if mode==Strictness.OBSERVED_ONLY and any(r['available_bound'] is None for r in candidates):
            if any(r['available_bound'] is not None for r in candidates):
                ambiguous.append({'event_id':event['id'],'revision_ids':sorted(r['id'] for r in candidates)})
                continue
            field='observed_time'
        newest=max(r[field] for r in candidates)
        leaders=[r for r in candidates if r[field]==newest]
        # Same-time lineage must have one provable head; never choose by row order or ID.
        ancestors=set()
        by_id={r['id']:r for r in candidates}
        for r in leaders:
            parent=r['supersedes']; visited=set()
            while parent in by_id and parent not in visited:
                ancestors.add(parent); visited.add(parent); parent=by_id[parent]['supersedes']
        leaders=[r for r in leaders if r['id'] not in ancestors]
        if len(leaders)>1:
            # Repeated observations of identical source bytes/semantic facts are
            # equivalent evidence, not conflicting revisions. Keep the earliest
            # actual observation, retaining every retrieval/revision in history.
            signatures=[]
            for r in leaders:
                raw=get(path,'raw',r['raw_id'])
                semantic={k:v for k,v in r['document'].items() if k not in {'raw_id','observed_time'}}
                signatures.append(digest([semantic,raw['sha256']]))
            if len(set(signatures))==1:
                leaders=[min(leaders,key=lambda r:(r['observed_time'],r['id']))]
        if len(leaders)!=1:
            ambiguous.append({'event_id':event['id'],'revision_ids':sorted(r['id'] for r in leaders)})
        else: selected.append(leaders[0])
    query={'as_of':cutoff,'strictness':mode.value,'identity_id':identity_id,'data_type':data_type,'source_id':source_id,'policy_version':'pit-query-v1'}
    return {'query':query,'status':'ambiguous' if ambiguous else 'ok','revisions':sorted(selected,key=lambda r:r['event_id']),
            'excluded':excluded,'ambiguous':ambiguous}


def input_manifest(path,**query):
    result=get_as_of(path,**query)
    if result['ambiguous']: raise ValueError('Ambiguous as-of query cannot become experiment input')
    entries=[]
    for r in result['revisions']:
        event=get(path,'events',r['event_id']); raw=get(path,'raw',r['raw_id'])
        entries.append({'revision_id':r['id'],'fingerprint':r['fingerprint'],'event_id':r['event_id'],'source_id':r['source_id'],
                        'identity_id':event['identity_id'],'raw_id':raw['id'],'raw_fingerprint':raw['fingerprint'],
                        'raw_sha256':raw['sha256'],'availability':r['document']['availability']})
    doc={'version':'pit-input-v1','query':result['query'],'entries':entries}
    return {**doc,'fingerprint':digest(doc)}


def leakage_check(path,manifest):
    checks=[]
    def add(status,reason,**details): checks.append({'status':status,'reason':reason,**details})
    if not isinstance(manifest,dict) or manifest.get('version')!='pit-input-v1':
        return {'status':'unverifiable','checks':[{'status':'unverifiable','reason':'No supported PIT manifest'}]}
    if digest({k:v for k,v in manifest.items() if k!='fingerprint'})!=manifest.get('fingerprint'):
        add('invalid','Input manifest fingerprint mismatch')
    try:
        query=manifest['query']
        if query.get('policy_version')!='pit-query-v1': raise ValueError('Unsupported query policy')
        result=get_as_of(path,**{k:v for k,v in query.items() if k!='policy_version'})
    except (ValueError,KeyError,TypeError) as exc:
        return {'status':'unverifiable','checks':checks+[{'status':'unverifiable','reason':'Missing/invalid query: '+str(exc)}]}
    if result['ambiguous']: add('invalid','Query now resolves ambiguously',evidence=result['ambiguous'])
    expected={r['id'] for r in result['revisions']}
    actual=[v.get('revision_id') for v in manifest.get('entries',[])]
    if len(actual)!=len(set(actual)): add('invalid','Duplicate input revision')
    if set(actual)!=expected: add('invalid','Selected revisions differ from historical as-of query',expected=sorted(expected),actual=actual)
    for entry in manifest.get('entries',[]):
        try:
            revision=get(path,'revisions',entry['revision_id']); raw=get(path,'raw',revision['raw_id'])
            event=get(path,'events',revision['event_id'])
        except KeyError:
            add('invalid','Missing PIT revision/raw/event',revision_id=entry.get('revision_id')); continue
        expected_fields={'fingerprint':revision['fingerprint'],'event_id':revision['event_id'],'source_id':revision['source_id'],
                         'identity_id':event['identity_id'],'raw_id':raw['id'],'raw_fingerprint':raw['fingerprint'],'raw_sha256':raw['sha256'],
                         'availability':revision['document']['availability']}
        if any(entry.get(k)!=v for k,v in expected_fields.items()) or digest(revision['document'])!=revision['fingerprint'] or digest(raw['document'])!=raw['fingerprint']:
            add('invalid','Revision/raw identity or fingerprint mismatch',revision_id=revision['id'])
        if not eligible(revision,query['as_of'],query['strictness']):
            add('invalid','Future-known or disallowed availability',revision_id=revision['id'])
        kind=revision['availability_kind']
        if kind=='unknown' and query['strictness']!='observed_only': add('unverifiable','Missing historical availability',revision_id=revision['id'])
        elif kind!='exact' or query['strictness']=='observed_only': add('warning','Explicit proxy/date/observation policy; not exact historical proof',revision_id=revision['id'])
        else: add('safe','Exact availability satisfies cutoff',revision_id=revision['id'])
    status=next((s for s in ('invalid','unverifiable','warning') if any(c['status']==s for c in checks)),'safe')
    return {'status':status,'checks':checks}
