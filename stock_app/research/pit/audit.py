"""Offline PIT integrity audit and reusable reconciliation findings."""
from collections import Counter
from hashlib import sha256
from pathlib import Path
from ..forecast_identity import digest
from ..integrity import reader
from .store import list_rows
from .temporal import instant, bound
from .schema import TABLES


def audit(path,*,depth='metadata',as_of=None):
    if depth not in {'metadata','artifact'}: raise ValueError('Invalid depth')
    data={t.removeprefix('pit_'):list_rows(path,t.removeprefix('pit_')) for t in TABLES}
    issues=[]
    def add(category,identity,reason,severity='error'):
        issues.append({'category':category,'id':identity,'reason':reason,'severity':severity})
    with reader(path) as db:
        for row in db.execute('PRAGMA foreign_key_check'):
            if str(row[0]).startswith('pit_'): add('orphan_reference',str(row[1]),str(tuple(row)))
    for kind,values in data.items():
        for row in values:
            if digest(row['document'])!=row['fingerprint']: add('fingerprint_mismatch',row['id'],'Stored content fingerprint differs')
            doc=row['document']
            prefix={'sources':'src-','identifiers':'alias-','raw':'raw-','events':'evt-','revisions':'rev-'}.get(kind,doc.get('kind','')+'-')
            identity_value=([doc['provider'],doc['feed']] if kind=='sources' else [doc['kind'],doc['key']] if kind=='identities' else [doc['source_id'],doc['data_type'],doc['source_key']] if kind=='events' else doc)
            if row['id']!=prefix+digest(identity_value): add('identity_mismatch',row['id'],'Deterministic record identity differs')
            for field in ('source_id','event_id','raw_id','observed_time','supersedes','availability_kind','sha256','identity_id','parent_id','namespace','value','scope','valid_from','valid_to'):
                if field in row and field in doc and row[field]!=doc[field]: add('identity_mismatch',row['id'],'Column/document disagreement: '+field)
    for raw in data['raw']:
        if raw['observed_time']>raw['ingested_time']: add('impossible_timestamp',raw['id'],'Observation follows ingestion')
        if depth=='artifact':
            file=Path(raw['document']['path'])
            try:
                if sha256(file.read_bytes()).hexdigest()!=raw['sha256']: add('checksum_mismatch',raw['id'],'Raw bytes changed')
            except FileNotFoundError: add('missing_raw_evidence',raw['id'],'Raw bytes absent')
            except OSError: add('unreadable_raw_evidence',raw['id'],'Raw bytes unreadable')
    by_id={r['id']:r for r in data['revisions']}
    raw_ids={r['id'] for r in data['raw']}
    for revision in data['revisions']:
        doc=revision['document']
        if revision['raw_id'] not in raw_ids: add('missing_raw_evidence',revision['id'],'Raw record absent')
        if digest(doc['payload'])!=doc['payload_fingerprint']: add('fingerprint_mismatch',revision['id'],'Payload fingerprint mismatch')
        if doc['availability']['kind']!=revision['availability_kind']: add('identity_mismatch',revision['id'],'Availability kind column differs')
        try:
            if instant(revision['observed_time'])>instant(revision['ingested_time']) or bound(doc['availability'],revision['observed_time'])!=revision['available_bound']:
                add('impossible_timestamp',revision['id'],'Temporal columns disagree with contract')
        except (ValueError,TypeError): add('impossible_timestamp',revision['id'],'Invalid temporal contract')
        parent=by_id.get(revision['supersedes'])
        if revision['supersedes'] and (parent is None or parent['event_id']!=revision['event_id']):
            add('conflicting_lineage',revision['id'],'Supersedes must refer to same logical event')
        if doc.get('relations',{}).get('amends_event') and not any(e['id']==doc['relations']['amends_event'] for e in data['events']):
            add('conflicting_lineage',revision['id'],'Original filing absent')
        if doc.get('payload',{}).get('lineage_status')=='unresolved': add('ambiguous_amendment',revision['id'],'Amendment original not proven','warning')
    aliases=data['identifiers']
    for i,left in enumerate(aliases):
        for right in aliases[i+1:]:
            if (left['namespace'],left['value'],left['scope'])!=(right['namespace'],right['value'],right['scope']) or left['identity_id']==right['identity_id']: continue
            a,b=left['valid_from'],left['valid_to']; c,d=right['valid_from'],right['valid_to']
            if not a or not c or (b is None and not left['document'].get('open_ended')) or (d is None and not right['document'].get('open_ended')): add('ambiguous_identifier',left['id']+':'+right['id'],'Unknown effective periods','warning')
            elif (d is None or a<d) and (b is None or c<b): add('overlapping_identifier_periods',left['id']+':'+right['id'],'Alias maps to different identities over overlapping periods')
    if depth=='artifact':
        paths={str(Path(r['document']['path']).resolve()) for r in data['raw']}
        for file in (Path(path).parent/'pit-raw').glob('*'):
            if file.is_file() and str(file.resolve()) not in paths: add('orphan_raw_file',file.name,'No committed retrieval references these bytes','warning')
    duplicate_sources=len(data['sources'])-len({(s['provider'],s['feed']) for s in data['sources']})
    return {'status':'attention' if issues else 'ok','counts':{k:len(v) for k,v in data.items()},'depth':depth,'issues':issues,
            'availability':dict(Counter(r['availability_kind'] for r in data['revisions'])),
            'duplicate_provider_ids':duplicate_sources,
            'future_known_revisions':sum(bool(r['available_bound'] and r['available_bound']>instant(as_of)) for r in data['revisions']) if as_of else None,
            'as_of':as_of,'network_used':False}
