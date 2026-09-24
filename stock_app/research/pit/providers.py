"""Small offline SEC/ALFRED contracts. No HTTP client or backfill scheduler."""
import json
import re
from .store import get
from .temporal import Availability, day, instant


def source(pit,provider,feed):
    capabilities={'SEC':{'acceptance':'proxy','requires_user_agent':True,'max_requests_per_second':10,'public_data_api_key_required':False},
                  'FRED_ALFRED':{'vintages':'date_level','intraday_availability':'not_proven'},
                  'YAHOO_FINANCE':{'history':'current_vintage'}}
    if provider not in capabilities: raise ValueError('Unknown built-in provider contract')
    return pit.source(provider=provider,feed=feed,source_type='public_data',schema_version='offline-v1',temporal_capabilities=capabilities[provider])


def _raw(pit,source_id,resource,payload,observed,parser):
    raw=pit.raw(source_id=source_id,resource=resource,payload=payload,observed_time=observed,parser_version=parser)
    return raw,json.loads(payload)


def _entity_cik(pit,entity_id,cik,source_id):
    if get(pit.store.path,'identities',entity_id)['kind']!='entity': raise ValueError('SEC filer must be an entity')
    if not str(cik).isdigit() or len(str(cik))>10: raise ValueError('Invalid CIK')
    normalized=str(cik).zfill(10)
    from .store import resolve_identifier
    matches=resolve_identifier(pit.store.path,normalized,namespace='cik')['matches']
    if not matches or any(r['identity_id']!=entity_id for r in matches):
        raise ValueError('Explicit unambiguous CIK/entity mapping required')
    return normalized


def sec_filing(pit,*,entity_id,source_id,resource,payload,observed_time,original_event_id=None,lineage_evidence=None):
    if get(pit.store.path,'sources',source_id)['provider']!='SEC': raise ValueError('SEC source required')
    raw,row=_raw(pit,source_id,resource,payload,observed_time,'sec-filing-v1')
    cik=_entity_cik(pit,entity_id,row['cik'],source_id)
    accession=row['accessionNumber']
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession): raise ValueError('Invalid accession number')
    form=row['form']; amended=form.endswith('/A')
    if not form: raise ValueError('SEC form required')
    if original_event_id:
        original=get(pit.store.path,'events',original_event_id)
        if not amended or not lineage_evidence or original['identity_id']!=entity_id or original['data_type']!='sec_filing':
            raise ValueError('Amendment lineage requires explicit same-entity filing evidence')
        from .store import list_rows
        originals=[r for r in list_rows(pit.store.path,'revisions') if r['event_id']==original_event_id]
        if not originals or any(r['document']['payload'].get('form') != form.removesuffix('/A') for r in originals):
            raise ValueError('Amendment and original form families disagree')
    acceptance=row.get('acceptanceDateTime')
    acceptance=instant(acceptance) if acceptance else None
    normalized={'cik':cik,'accession_number':accession,'form':form,'filing_date':day(row['filingDate']),
                'acceptance_datetime':acceptance,'period_of_report':day(row['reportDate']) if row.get('reportDate') else None,
                'primary_document':row.get('primaryDocument'),'amendment':amended,
                'lineage_status':'supported' if original_event_id else 'unresolved' if amended else 'not_applicable',
                'original_event_id':original_event_id,'lineage_evidence':lineage_evidence}
    event=pit.event(source_id=source_id,source_key=accession,data_type='sec_filing',identity_id=entity_id)
    if original_event_id==event['id']: raise ValueError('Filing cannot amend itself')
    availability=Availability('proxy','sec_acceptance_proxy',acceptance,policy='sec_acceptance_proxy_v1') if acceptance else Availability('unknown','unknown')
    revision=pit.revision(event_id=event['id'],raw_id=raw['id'],source_record_id=accession,payload=normalized,availability=availability,
        event_time={'precision':'date','value':normalized['period_of_report']} if normalized['period_of_report'] else None,
        source_time={'precision':'instant','value':acceptance} if acceptance else {'precision':'date','value':normalized['filing_date']},
        parser_version='sec-filing-v1',relations={'amends_event':original_event_id,'evidence':lineage_evidence} if original_event_id else {})
    return revision


def sec_xbrl(pit,*,entity_id,source_id,resource,payload,observed_time):
    if get(pit.store.path,'sources',source_id)['provider']!='SEC': raise ValueError('SEC source required')
    raw,row=_raw(pit,source_id,resource,payload,observed_time,'sec-xbrl-v1')
    cik=_entity_cik(pit,entity_id,row['cik'],source_id)
    identity={k:row.get(k) for k in ('taxonomy','concept','unit','start','end','instant','dimensions')}
    if not all(identity[k] for k in ('taxonomy','concept','unit')): raise ValueError('XBRL taxonomy/concept/unit required')
    if bool(row.get('instant'))==bool(row.get('end')): raise ValueError('XBRL instant or duration end required')
    for key in ('start','end','instant'):
        if identity[key] is not None: identity[key]=day(identity[key])
    identity.update(entity_id=entity_id)
    from ..forecast_identity import digest
    event=pit.event(source_id=source_id,source_key=digest(identity),data_type='sec_xbrl',identity_id=entity_id)
    # Companyfacts filed date alone does not establish precise historical availability.
    if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', row['accession']): raise ValueError('Invalid XBRL accession')
    if 'value' not in row or isinstance(row['value'], bool): raise ValueError('XBRL value required')
    from decimal import Decimal
    if not Decimal(str(row['value'])).is_finite(): raise ValueError('Finite XBRL value required')
    normalized={**row,**identity,'cik':cik}
    return pit.revision(event_id=event['id'],raw_id=raw['id'],source_record_id=row['accession'],payload=normalized,
        availability=Availability('unknown','companyfacts_filed_date_not_public_availability'),
        event_time={'precision':'date','value':row.get('instant') or row.get('start') or row['end']},
        event_end={'precision':'date','value':row['end']} if row.get('start') else None,
        source_time={'precision':'date','value':day(row['filed'])} if row.get('filed') else None,parser_version='sec-xbrl-v1')


def alfred_observation(pit,*,source_id,resource,payload,observed_time,date_timezone,supersedes=None):
    if get(pit.store.path,'sources',source_id)['provider']!='FRED_ALFRED': raise ValueError('ALFRED source required')
    raw,row=_raw(pit,source_id,resource,payload,observed_time,'alfred-observation-v1')
    for key in ('observation_date','realtime_start','realtime_end','vintage_date'): row[key]=day(row[key])
    if row['vintage_date'] > instant(observed_time)[:10]: raise ValueError('Vintage follows local observation date')
    if not row.get('series_id'): raise ValueError('ALFRED series identity required')
    if row['realtime_end']<row['realtime_start']: raise ValueError('Invalid realtime interval')
    if row['vintage_date']<row['realtime_start'] or row['vintage_date']>row['realtime_end']:
        raise ValueError('Requested vintage must lie in returned realtime interval')
    # Keep '.' missing values distinct from zero and decimal strings losslessly.
    if row['value']!='.':
        from decimal import Decimal
        if not Decimal(str(row['value'])).is_finite(): raise ValueError('Nonfinite observation')
    event=pit.event(source_id=source_id,source_key=row['series_id']+':'+row['observation_date'],data_type='macro_observation')
    return pit.revision(event_id=event['id'],raw_id=raw['id'],source_record_id=row['series_id']+':'+row['observation_date']+':'+row['realtime_start'],
        payload=row,availability=Availability('date_level','provider_realtime_period',row['realtime_start'],date_timezone,'end_of_day_v1'),
        event_time={'precision':'date','value':row['observation_date']},source_time={'precision':'date','value':row['vintage_date']},
        supersedes=supersedes,parser_version='alfred-observation-v1')
