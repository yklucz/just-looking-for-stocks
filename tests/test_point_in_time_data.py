"""Offline synthetic PIT evidence, cutoff, identity, SQL and integration tests."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from stock_app.research.store import ResearchStore
from stock_app.research.forecast_identity import digest,canonical_json
from stock_app.research.pit.store import PITStore,get,list_rows,resolve_identifier
from stock_app.research.pit.temporal import Availability,instant,day,bound
from stock_app.research.pit.query import get_as_of,input_manifest,leakage_check
from stock_app.research.pit.audit import audit
from stock_app.research.pit.providers import source,sec_filing,sec_xbrl,alfred_observation
from stock_app.research.pit.integration import with_pit_inputs,evidence_checks,operational_handler
from stock_app.research.pit.cli import main
from stock_app.research.pit.migration import migrate,inventory

NOW='2026-09-23T00:00:00Z'
OBS='2025-04-01T12:00:00Z'
FIXTURES=Path(__file__).parent/'fixtures/pit'


@pytest.fixture
def pit(tmp_path):
    return PITStore(ResearchStore(tmp_path/'research.sqlite3'),clock=lambda:NOW)


@pytest.fixture
def src(pit):
    return pit.source(provider='SYNTHETIC',feed='test',source_type='fixture',schema_version='1',temporal_capabilities={'exact':True})


@pytest.fixture
def entity(pit):
    return pit.identity(kind='entity',key='synthetic-company',name='Synthetic company',evidence='Synthetic fixture; no real mapping')


@pytest.fixture
def security(pit,entity):
    return pit.identity(kind='security',key='synthetic-common',name='Synthetic common shares',parent_id=entity['id'],evidence='Synthetic fixture')


def raw(pit,src,payload=b'{"value":100}',observed=OBS):
    return pit.raw(source_id=src['id'],resource='fixture:one',payload=payload,observed_time=observed,parser_version='test-v1')


def revision(pit,src,*,value=100,available='2025-01-01T00:00:00Z',kind='exact',supersedes=None,observed=OBS,event=None,**kwargs):
    evidence=raw(pit,src,canonical_json({'value':value}).encode(),observed)
    event=event or pit.event(source_id=src['id'],source_key='one',data_type='test')
    availability=Availability(kind,'source_declared',available) if kind=='exact' else Availability(kind,'explicit_proxy',available,policy='test-proxy-v1') if kind=='proxy' else Availability(kind,'local_observation' if kind=='observed_only' else 'unknown')
    return pit.revision(event_id=event['id'],raw_id=evidence['id'],source_record_id='source-one',payload={'value':value},availability=availability,supersedes=supersedes,**kwargs)


def query(pit,time='2025-02-01T00:00:00Z',mode='strict',**kwargs):
    return get_as_of(pit.store.path,as_of=time,strictness=mode,**kwargs)


def manifest(pit,**kwargs):
    return input_manifest(pit.store.path,as_of=kwargs.pop('as_of','2025-02-01T00:00:00Z'),strictness=kwargs.pop('strictness','strict'),**kwargs)


def test_source_identity(pit,src):
    assert src['id'].startswith('src-') and src['document']['feed']=='test'


def test_duplicate_source_deduplicated(pit,src):
    assert pit.source(provider='SYNTHETIC',feed='test',source_type='fixture',schema_version='1',temporal_capabilities={'exact':True})==src
    assert len(list_rows(pit.store.path,'sources'))==1


def test_source_change_cannot_redefine_identity(pit,src):
    with pytest.raises(ValueError):
        pit.source(provider='SYNTHETIC',feed='test',source_type='fixture',schema_version='2',temporal_capabilities={})


def test_distinct_provider_feeds(pit):
    a=source(pit,'SEC','submissions');b=source(pit,'SEC','companyfacts')
    assert a['id']!=b['id']


def test_entity_and_security_distinct(entity,security):
    assert entity['kind']=='entity' and security['kind']=='security' and security['parent_id']==entity['id']


def test_multiple_securities_per_issuer(pit,entity,security):
    other=pit.identity(kind='security',key='preferred',name='Preferred shares',parent_id=entity['id'],evidence='Synthetic fixture')
    assert other['id']!=security['id'] and other['parent_id']==security['parent_id']


def test_listing_identity(pit,security):
    listing=pit.identity(kind='listing',key='listing-one',name='Primary listing',parent_id=security['id'],exchange='XNAS',evidence='Synthetic fixture')
    assert listing['parent_id']==security['id'] and listing['kind']=='listing'


@pytest.mark.parametrize('kind,parent',[('security',None),('listing',None),('entity','absent')])
def test_invalid_identity_parents(pit,kind,parent):
    with pytest.raises(sqlite3.IntegrityError):
        pit.identity(kind=kind,key='bad',name='Invalid identity',parent_id=parent,exchange='XNAS',evidence='test')


def test_unknown_ticker_compatibility(pit):
    r=resolve_identifier(pit.store.path,'aapl')
    assert r['status']=='legacy_symbol' and r['identity_id'] is None and not list_rows(pit.store.path,'identities')


def alias(pit,src,security,**kwargs):
    return pit.identifier(identity_id=security['id'],source_id=src['id'],namespace='ticker',value='TEST',scope='XNAS',evidence='Synthetic alias evidence',**kwargs)


def test_known_ticker_resolves(pit,src,security):
    alias(pit,src,security)
    assert resolve_identifier(pit.store.path,'TEST')['identity_id']==security['id']


@pytest.mark.parametrize('when,status',[('2024-12-31','legacy_symbol'),('2025-01-01','resolved'),('2025-02-28','resolved'),('2025-03-01','legacy_symbol')])
def test_identifier_half_open_period(pit,src,security,when,status):
    alias(pit,src,security,valid_from='2025-01-01',valid_to='2025-03-01')
    assert resolve_identifier(pit.store.path,'TEST',on_date=when)['status']==status


def test_unknown_history_not_invented(pit,src,security):
    alias(pit,src,security)
    r=resolve_identifier(pit.store.path,'TEST',on_date='2020-01-01')
    assert r['status']=='ambiguous' and r['identity_id'] is None


def test_ambiguous_identifier_mapping(pit,src,entity,security):
    alias(pit,src,security,valid_from='2025-01-01',open_ended=True)
    other=pit.identity(kind='security',key='other',name='Other shares',parent_id=entity['id'],evidence='Synthetic fixture')
    alias(pit,src,other,valid_from='2025-01-01',open_ended=True)
    assert resolve_identifier(pit.store.path,'TEST')['status']=='ambiguous'
    assert any(i['category']=='overlapping_identifier_periods' for i in audit(pit.store.path)['issues'])


def test_cik_cannot_be_security(pit,src,security):
    with pytest.raises(sqlite3.IntegrityError):
        pit.identifier(identity_id=security['id'],source_id=src['id'],namespace='cik',value='1234567',evidence='test')


def test_raw_persistence_checksum(pit,src):
    r=raw(pit,src)
    assert Path(r['document']['path']).read_bytes()==b'{"value":100}'
    assert r['sha256']==sha256(b'{"value":100}').hexdigest()


def test_raw_identical_retrieval_idempotence(pit,src):
    assert raw(pit,src)==raw(pit,src)
    assert len(list_rows(pit.store.path,'raw'))==1


def test_raw_reobservation_deduplicates_bytes_only(pit,src):
    a=raw(pit,src);b=raw(pit,src,observed='2025-05-01T00:00:00Z')
    assert a['id']!=b['id'] and a['document']['path']==b['document']['path']


def test_changed_raw_preserves_both(pit,src):
    a=raw(pit,src);b=raw(pit,src,b'{"value":103}')
    assert a['id']!=b['id'] and Path(a['document']['path']).exists()


def test_observed_after_ingestion_refused(pit,src):
    with pytest.raises(ValueError):raw(pit,src,observed='2030-01-01T00:00:00Z')


def test_event_logical_identity(pit,src):
    a=pit.event(source_id=src['id'],source_key='one',data_type='test')
    assert pit.event(source_id=src['id'],source_key='one',data_type='test')==a


def test_revision_creation_determinism(pit,src):
    a=revision(pit,src)
    assert revision(pit,src)==a and digest(a['document'])==a['fingerprint']


def test_supersession_retains_original(pit,src):
    a=revision(pit,src);b=revision(pit,src,value=103,available='2025-03-01T00:00:00Z',supersedes=a['id'])
    assert b['supersedes']==a['id'] and len(list_rows(pit.store.path,'revisions'))==2


def test_cross_event_supersession_refused(pit,src):
    a=revision(pit,src);event=pit.event(source_id=src['id'],source_key='other',data_type='test')
    with pytest.raises(sqlite3.IntegrityError):revision(pit,src,event=event,supersedes=a['id'])


def test_five_times_remain_distinct(pit,src):
    a=revision(pit,src,event_time={'precision':'date','value':'2024-12-31'},source_time={'precision':'instant','value':'2025-01-01T00:00:00Z'})
    d=a['document']
    assert d['event_time']['value']=='2024-12-31'
    assert d['source_time']['value']==instant('2025-01-01T00:00:00Z')
    assert a['available_bound']==d['availability']['value']
    assert d['observed_time']==instant(OBS) and a['ingested_time']==instant(NOW)


@pytest.mark.parametrize('bad',['2025-01-01','2025-01-01T12:00:00',None])
def test_exact_time_never_infers_zone(bad):
    with pytest.raises(ValueError):Availability('exact','source_declared',bad).document()


def test_exact_cannot_masquerade_as_proxy():
    with pytest.raises(ValueError):Availability('exact','sec_acceptance_proxy','2025-01-01T00:00:00Z').document()


def test_unknown_availability_has_no_invented_time(pit,src):
    r=revision(pit,src,kind='unknown')
    assert r['available_bound'] is None and query(pit)['revisions']==[]


def test_strict_rejects_proxy(pit,src):
    revision(pit,src,kind='proxy')
    assert query(pit)['revisions']==[]


def test_proxy_policy_accepts_labeled_proxy(pit,src):
    r=revision(pit,src,kind='proxy')
    assert query(pit,mode='allow_proxy')['revisions'][0]['id']==r['id']


def test_observed_only_uses_observation_not_historical_date(pit,src):
    revision(pit,src)
    assert query(pit,mode='observed_only')['revisions']==[]
    assert len(query(pit,time=OBS,mode='observed_only')['revisions'])==1


def test_asof_historical_not_latest(pit,src):
    a=revision(pit,src);b=revision(pit,src,value=103,available='2025-03-01T00:00:00Z',supersedes=a['id'])
    assert query(pit)['revisions'][0]['id']==a['id']
    assert query(pit,time='2025-03-02T00:00:00Z')['revisions'][0]['id']==b['id']


def test_cutoff_required(pit):
    with pytest.raises(TypeError):get_as_of(pit.store.path,strictness='strict')


def test_ambiguous_same_time_revisions_not_guessed(pit,src):
    revision(pit,src);revision(pit,src,value=103)
    result=query(pit)
    assert result['ambiguous'] and not result['revisions']
    with pytest.raises(ValueError):manifest(pit)


def test_date_precision_conservative_boundary():
    a=Availability('date_level','provider_realtime_period','2025-03-09','America/Chicago','end_of_day_v1').document()
    assert a['value']=='2025-03-09' and bound(a,OBS)=='2025-03-10T05:00:00+00:00'


@pytest.mark.parametrize('kind',['exact','proxy'])
def test_future_availability_not_observed_yet(pit,src,kind):
    with pytest.raises(ValueError):revision(pit,src,kind=kind,available='2025-05-01T00:00:00Z')


def test_current_vintage_cannot_claim_exact(pit,src):
    with pytest.raises(ValueError):revision(pit,src,data_vintage='current_vintage')


def test_current_vintage_observation_visible_only_later(pit,src):
    revision(pit,src,kind='observed_only',data_vintage='current_vintage')
    assert query(pit,time=OBS,mode='strict')['revisions']==[]
    assert query(pit,time=OBS,mode='allow_proxy')['revisions']==[]
    assert len(query(pit,time=OBS,mode='observed_only')['revisions'])==1


@pytest.fixture
def sec(pit,entity):
    s=source(pit,'SEC','submissions')
    pit.identifier(identity_id=entity['id'],source_id=s['id'],namespace='cik',value='1234567',evidence='Synthetic fixture CIK mapping')
    return s


def filing(pit,sec,entity,name='sec-filing.json',**kwargs):
    return sec_filing(pit,entity_id=entity['id'],source_id=sec['id'],resource='fixture:'+name,payload=(FIXTURES/name).read_bytes(),observed_time=OBS,**kwargs)


def test_sec_accession_not_security_identity(pit,sec,entity):
    r=filing(pit,sec,entity)
    assert get(pit.store.path,'events',r['event_id'])['identity_id']==entity['id']
    assert r['source_record_id']=='0001234567-25-000001'
    assert not [r for r in list_rows(pit.store.path,'identities') if r['kind']=='security']


def test_sec_acceptance_is_proxy_not_exact(pit,sec,entity):
    r=filing(pit,sec,entity)
    assert r['availability_kind']=='proxy' and r['document']['source_time']['precision']=='instant'
    assert query(pit,time=OBS)['revisions']==[]


def test_sec_amendment_preserves_original(pit,sec,entity):
    a=filing(pit,sec,entity)
    b=filing(pit,sec,entity,'sec-amendment.json',original_event_id=a['event_id'],lineage_evidence='Explicit synthetic original accession reference')
    assert b['event_id']!=a['event_id'] and b['document']['relations']['amends_event']==a['event_id']
    assert len(list_rows(pit.store.path,'events'))==2


def test_sec_ambiguous_amendment_unresolved(pit,sec,entity):
    b=filing(pit,sec,entity,'sec-amendment.json')
    assert b['document']['payload']['lineage_status']=='unresolved'
    assert any(i['category']=='ambiguous_amendment' for i in audit(pit.store.path)['issues'])


@pytest.mark.parametrize('field,new',[('concept','Assets'),('unit','EUR'),('start','2023-01-01'),('dimensions',{'Segment':'Retail'})])
def test_xbrl_identity_dimensions(pit,sec,entity,field,new):
    args=dict(entity_id=entity['id'],source_id=sec['id'],resource='fixture:xbrl',observed_time=OBS)
    original=(FIXTURES/'sec-xbrl.json').read_bytes()
    a=sec_xbrl(pit,payload=original,**args)
    row=json.loads(original);row[field]=new
    b=sec_xbrl(pit,payload=canonical_json(row).encode(),**args)
    assert a['event_id']!=b['event_id'] and a['availability_kind']=='unknown'


def alfred(pit,name='alfred-a.json',**kwargs):
    src=source(pit,'FRED_ALFRED','observations')
    return alfred_observation(pit,source_id=src['id'],resource='fixture:'+name,payload=(FIXTURES/name).read_bytes(),observed_time=OBS,date_timezone='America/Chicago',**kwargs)


def test_alfred_vintages_preserved(pit):
    a=alfred(pit);b=alfred(pit,'alfred-b.json',supersedes=a['id'])
    assert a['event_id']==b['event_id'] and a['id']!=b['id']
    assert a['document']['payload']['value']=='100' and b['document']['payload']['value']=='103'


@pytest.mark.parametrize('cutoff,value',[('2025-02-15T00:00:00Z','100'),('2025-03-02T12:00:00Z','103')])
def test_alfred_historical_queries(pit,cutoff,value):
    a=alfred(pit);alfred(pit,'alfred-b.json',supersedes=a['id'])
    r=query(pit,time=cutoff,mode='allow_proxy')['revisions'][0]
    assert r['document']['payload']['value']==value and r['document']['availability']['value'] in {'2025-02-01','2025-03-01'}


def test_alfred_no_invented_intraday_time(pit):
    a=alfred(pit)
    assert a['document']['source_time']=={'precision':'date','value':'2025-02-01'}
    assert query(pit,time='2025-02-01T12:00:00Z',mode='allow_proxy')['revisions']==[]
    assert query(pit,time=OBS)['revisions']==[]


def test_leakage_safe(pit,src):
    revision(pit,src)
    assert leakage_check(pit.store.path,manifest(pit))['status']=='safe'


def test_leakage_warning_for_proxy(pit,src):
    revision(pit,src,kind='proxy')
    assert leakage_check(pit.store.path,manifest(pit,strictness='allow_proxy'))['status']=='warning'


def test_missing_temporal_manifest_unverifiable(pit):
    assert leakage_check(pit.store.path,{})['status']=='unverifiable'


def test_leakage_newer_revision_used_early(pit,src):
    a=revision(pit,src);revision(pit,src,value=103,available='2025-03-01T00:00:00Z',supersedes=a['id'])
    m=manifest(pit,as_of=OBS);m['query']['as_of']='2025-02-01T00:00:00+00:00';m['fingerprint']=digest({k:v for k,v in m.items() if k!='fingerprint'})
    assert leakage_check(pit.store.path,m)['status']=='invalid'


def test_leakage_proxy_under_strict(pit,src):
    revision(pit,src,kind='proxy')
    m=manifest(pit,strictness='allow_proxy');m['query']['strictness']='strict';m['fingerprint']=digest({k:v for k,v in m.items() if k!='fingerprint'})
    assert leakage_check(pit.store.path,m)['status']=='invalid'


def test_leakage_observed_later_cannot_be_historical_proof(pit,src):
    revision(pit,src,kind='observed_only')
    m=manifest(pit,as_of=OBS,strictness='observed_only');m['query']['strictness']='strict'
    m['fingerprint']=digest({k:v for k,v in m.items() if k!='fingerprint'})
    assert leakage_check(pit.store.path,m)['status']=='invalid'


def test_integrity_missing_revision(pit,src):
    revision(pit,src);m=manifest(pit);m['entries'][0]['revision_id']='absent'
    assert any(c['status']=='mismatch' for c in evidence_checks(pit.store.path,m))


def test_integrity_fingerprint_mismatch(pit,src):
    revision(pit,src);m=manifest(pit);m['entries'][0]['fingerprint']='changed'
    assert leakage_check(pit.store.path,m)['status']=='invalid'


def test_raw_artifact_missing_offline(pit,src):
    r=revision(pit,src);m=manifest(pit)
    Path(get(pit.store.path,'raw',r['raw_id'])['document']['path']).unlink()
    assert any(c['status']=='missing' for c in evidence_checks(pit.store.path,m,depth='artifact'))


def test_audit_checksum_mismatch(pit,src):
    r=raw(pit,src);Path(r['document']['path']).write_bytes(b'changed')
    assert any(i['category']=='checksum_mismatch' for i in audit(pit.store.path,depth='artifact')['issues'])


def test_audit_readonly_no_network(pit,src):
    revision(pit,src)
    before=pit.store.path.read_bytes()
    with patch('socket.socket',side_effect=AssertionError('network forbidden')):
        assert audit(pit.store.path)['status']=='ok'
    assert pit.store.path.read_bytes()==before


def test_metadata_audit_no_file_reads(pit,src):
    raw(pit,src)
    with patch.object(Path,'read_bytes',side_effect=AssertionError('no bytes')):
        assert audit(pit.store.path)['status']=='ok'


def test_cli_asof(pit,src,capsys):
    revision(pit,src)
    main(['--database',str(pit.store.path),'as-of','--time','2025-02-01T00:00:00Z','--strictness','strict'])
    assert json.loads(capsys.readouterr().out)['revisions'][0]['document']['payload']['value']==100


def test_cli_legacy_symbol(pit,capsys):
    main(['--database',str(pit.store.path),'resolve','AAPL'])
    assert json.loads(capsys.readouterr().out)['status']=='legacy_symbol'


@pytest.mark.parametrize('endpoint',['sources','resolve/AAPL','as-of?as_of=2025-02-01T00:00:00Z&strictness=strict','audit'])
def test_readonly_api(pit,monkeypatch,endpoint):
    from flask import Flask
    from stock_app.research.api import api
    monkeypatch.setenv('STOCK_RESEARCH_ROOT',str(pit.store.path.parent))
    app=Flask(__name__);app.register_blueprint(api)
    with patch('stock_app.research.api.runtime',side_effect=AssertionError('runtime forbidden')):
        assert app.test_client().get('/api/research/pit/'+endpoint).status_code==200
    assert app.test_client().post('/api/research/pit/'+endpoint).status_code==405


def test_concurrent_identical_revision_insertion(pit,src):
    with ThreadPoolExecutor(max_workers=3) as pool:
        result=list(pool.map(lambda _:revision(pit,src),range(3)))
    assert len({r['id'] for r in result})==1 and len(list_rows(pit.store.path,'revisions'))==1


@pytest.mark.parametrize('table',['sources','identities','identifiers','raw','events','revisions'])
def test_sql_immutability(pit,src,security,table):
    alias(pit,src,security);revision(pit,src)
    for statement in (f'UPDATE pit_{table} SET document=document',f'DELETE FROM pit_{table}'):
        with pytest.raises(sqlite3.IntegrityError):
            with pit.store.connection() as db: db.execute(statement)


def test_migration_preserves_all_previous_tables_and_models(pit,tmp_path):
    file=tmp_path/'model.bin';file.write_bytes(b'original model')
    pit.store.put('models',{'id':'model','symbol':'TEST','task':'binary','artifact':str(file)})
    with pit.store.connection() as db:
        db.execute('INSERT INTO active_models VALUES(?,?,?)',('TEST','binary','model'))
        before=inventory(db)
    a=migrate(pit.store.path);b=migrate(pit.store.path)
    assert a['before']==a['after']==b['before']==b['after']==before
    assert Path(a['backup']).exists() and file.read_bytes()==b'original model'
    assert 'integrity_cases' in a['before']['tables']


def test_new_spec_and_run_pin_pit_manifest(pit,src,tmp_path):
    from stock_app.research.registry import Registry
    from stock_app.research.registry_execution import prepare
    from stock_app.research.integrity import verify_run
    from test_research_experiments import history,small_config
    registry=Registry(pit.store,clock=lambda:NOW)
    old,refs,_=prepare(registry,history(),{'ticker':'TEST','output':tmp_path/'out','feature_sets':('baseline',),'config':small_config()})
    revision(pit,src);m=manifest(pit)
    contract=with_pit_inputs(pit.store.path,old['document']['contract'],m)
    spec=registry.spec(hypothesis_id=old['hypothesis_id'],family_id=old['family_id'],contract=contract)
    assert 'pit_manifest' not in old['document']['contract']['data']
    with pytest.raises(ValueError):registry.run(spec_id=spec['id'],execution_key='no-provenance')
    run=registry.run(spec_id=spec['id'],execution_key='pit-future-consumer',provenance={'inputs':refs,'pit_manifest':m})
    result=verify_run(pit.store.path,run['id'])
    assert any(c['reference'].startswith('pit:') and c['status']=='verified' for c in result['checks'])


def test_reconciliation_surfaces_pit_without_auto_resolution(pit,src):
    from stock_app.research.integrity_reconcile import detect_cases
    r=raw(pit,src);Path(r['document']['path']).write_bytes(b'changed')
    cases=detect_cases(pit.store)
    assert any(c['category']=='pit_checksum_mismatch' and c['state']=='open' for c in cases)


def test_alfred_expired_vintage_no_latest_fallback(pit):
    alfred(pit)
    assert query(pit,time='2025-03-05T00:00:00Z',mode='allow_proxy')['revisions']==[]


def test_date_level_requires_explicit_zone_policy():
    with pytest.raises(ValueError):Availability('date_level','provider_realtime_period','2025-01-01').document()


def test_raw_retained_on_normalization_failure(pit,sec,entity):
    invalid=json.loads((FIXTURES/'sec-filing.json').read_text());invalid['accessionNumber']='invalid'
    with pytest.raises(ValueError):sec_filing(pit,entity_id=entity['id'],source_id=sec['id'],resource='invalid',payload=canonical_json(invalid).encode(),observed_time=OBS)
    assert len(list_rows(pit.store.path,'raw'))==1 and not list_rows(pit.store.path,'revisions')


def test_provider_cross_source_evidence_rejected(pit,src):
    other=source(pit,'SEC','submissions');r=raw(pit,other)
    e=pit.event(source_id=src['id'],source_key='event',data_type='test')
    with pytest.raises(ValueError):pit.revision(event_id=e['id'],raw_id=r['id'],source_record_id='x',payload={},availability=Availability('unknown','unknown'))


def test_prior_canonical_forecast_preserved_by_migration(pit):
    from stock_app.research.ledger import issue_forecast
    issue_forecast(pit.store,symbol='TEST',model_id='unknown-model',snapshot_id='unknown-snapshot',origin='2025-01-02',payload={'probability':.5,'origin_close':100.},issued_at='2025-01-02T22:00:00Z')
    result=migrate(pit.store.path)
    assert result['before']==result['after'] and result['before']['tables']['forecast_issuances']['count']==1
    assert result['before']['tables']['forecast_revisions']['count']==1


def test_operational_adapter_pins_effects_and_blocks_partial_failure(pit,src):
    from stock_app.jobs.core import UnsafeExecution
    from types import SimpleNamespace
    r=raw(pit,src);effects=[]
    context=SimpleNamespace(run_id='op',store=SimpleNamespace(get=lambda _: {'inputs':{'raw_id':r['id']}}),begin_effects=effects.append)
    def failed(pit,inputs):raise RuntimeError('partial failure')
    with pytest.raises(UnsafeExecution):operational_handler(pit,failed)(None,context)
    assert effects==[{'raw_id':r['id'],'raw_sha256':r['sha256']}]


def test_operational_registry_no_new_ingestion_schedules(pit):
    from stock_app.jobs.service import registry
    assert all(j.kind in {'refresh','forecast','experiment'} for j in registry(pit.store))


def test_pit_manifest_detects_current_cutoff_changed(pit,src):
    revision(pit,src);m=manifest(pit)
    m['query']['as_of']='2024-01-01T00:00:00Z'
    assert leakage_check(pit.store.path,m)['status']=='invalid'


def test_sql_source_uniqueness(pit,src):
    with pytest.raises(sqlite3.IntegrityError):
        with pit.store.connection() as db:
            db.execute('INSERT INTO pit_sources SELECT ?,provider,feed,fingerprint,created_at,document FROM pit_sources',('another',))


def test_sql_revision_foreign_key(pit,src):
    r=revision(pit,src)
    with pytest.raises(sqlite3.IntegrityError):
        with pit.store.connection() as db:
            db.execute("INSERT INTO pit_revisions SELECT ?,event_id,source_id,?,source_record_id,supersedes,?,availability_kind,available_bound,observed_time,ingested_time,document FROM pit_revisions",('bad','absent','different'))


def test_reobserving_old_vintage_does_not_override_newer_source_vintage(pit,src):
    a=revision(pit,src)
    b=revision(pit,src,value=103,available='2025-03-01T00:00:00Z',supersedes=a['id'])
    revision(pit,src,observed='2025-05-01T00:00:00Z')
    result=query(pit,time='2025-06-01T00:00:00Z',mode='observed_only')
    assert result['revisions'][0]['id']==b['id']


def test_mixed_unknown_revision_order_remains_ambiguous(pit,src):
    revision(pit,src);revision(pit,src,value=103,kind='unknown')
    assert query(pit,time=OBS,mode='observed_only')['status']=='ambiguous'


def test_identical_retrievals_do_not_manufacture_revision_ambiguity(pit,src):
    first=revision(pit,src)
    revision(pit,src,observed='2025-05-01T00:00:00Z')
    result=query(pit)
    assert result['status']=='ok' and result['revisions'][0]['id']==first['id']
    assert len(list_rows(pit.store.path,'revisions'))==2


def test_observed_query_does_not_reuse_expired_alfred_vintage(pit):
    alfred(pit)
    assert query(pit,time=OBS,mode='observed_only')['revisions']==[]



def test_unknown_alias_end_does_not_imply_infinite_history(pit,src,security):
    alias(pit,src,security,valid_from='2025-01-01')
    assert resolve_identifier(pit.store.path,'TEST',on_date='2026-01-01')['status']=='ambiguous'


def test_explicit_open_ended_alias_is_distinct_from_unknown_end(pit,src,security):
    alias(pit,src,security,valid_from='2025-01-01',open_ended=True)
    assert resolve_identifier(pit.store.path,'TEST',on_date='2026-01-01')['status']=='resolved'
