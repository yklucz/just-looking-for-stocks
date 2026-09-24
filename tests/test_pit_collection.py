"""Offline live-shaped contract tests. No provider access or real credentials."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from stock_app.research.store import ResearchStore
from stock_app.research.pit.store import PITStore, list_rows, get
from stock_app.research.pit.transport import Config, ProviderError, SECClient, ALFREDClient, RateLimiter
from stock_app.research.pit.collection import Collector, Scope, collection_rows, verify_raw, findings, coverage, status
from stock_app.research.pit.query import get_as_of, input_manifest, leakage_check
from stock_app.research.pit.audit import audit
from stock_app.research.pit import live_parsers
from stock_app.research.pit.cli import main

NOW = '2026-09-24T12:00:00+00:00'
KEY = 'a' * 32
CONFIG = Config(sec_user_agent='Offline Test test@example.invalid', fred_api_key=KEY)
CIK = '0000320193'  # Synthetic fixture assertion, not live identity verification.
SEC_SCOPE = Scope('sec', '2025-01-01', '2025-03-31')
ALFRED_SCOPE = Scope('alfred', '2025-01-01', '2025-03-31', series=('GDP',), observation_start='2025-01-01', observation_end='2025-03-31')


class Clock:
    def __init__(self):
        self.value = 0
        self.waits = []
    def monotonic(self):
        return self.value
    def sleep(self, value):
        self.waits.append(value)
        self.value += value
    def now(self):
        return datetime.fromisoformat(NOW)


def response(body, status=200, headers=None):
    return SimpleNamespace(content=json.dumps(body).encode() if not isinstance(body, bytes) else body,
                           status_code=status, headers=headers or {'Content-Type': 'application/json'}, close=Mock())


def filing_rows(amended=False):
    forms = ['10-Q', '10-Q/A'] if amended else ['10-Q']
    return {'accessionNumber': ['0000320193-25-000001', '0000320193-25-000002'][:len(forms)],
            'form': forms, 'filingDate': ['2025-02-01', '2025-03-01'][:len(forms)],
            'reportDate': ['2024-12-31'] * len(forms),
            'acceptanceDateTime': ['2025-02-01T12:00:00Z', '2025-03-01T12:00:00Z'][:len(forms)],
            'primaryDocument': ['sample.htm'] * len(forms)}


def sec_payload(url):
    if 'company_tickers' in url:
        return {'0': {'cik_str': 320193, 'ticker': 'AAPL', 'title': 'Fixture Apple'}}
    if 'companyconcept' in url:
        return {'cik': 320193, 'taxonomy': 'us-gaap', 'tag': 'Assets', 'units': {'USD': [
            {'end': '2024-12-31', 'val': 100, 'accn': '0000320193-25-000001', 'filed': '2025-02-01', 'form': '10-Q'}]}}
    return {'cik': 320193, 'tickers': ['AAPL'], 'exchanges': ['Nasdaq'], 'filings': {'recent': filing_rows(), 'files': []}}


def fred_payload(url, params):
    values = ['2025-02-01', '2025-03-01'] if 'vintagedates' in url else [
        {'date': '2025-01-01', 'realtime_start': '2025-02-01', 'realtime_end': '2025-02-28', 'value': '100'},
        {'date': '2025-01-01', 'realtime_start': '2025-03-01', 'realtime_end': '2025-03-31', 'value': '103'},
        {'date': '2025-02-01', 'realtime_start': '2025-03-01', 'realtime_end': '2025-03-31', 'value': '.'}]
    offset, limit = params['offset'], params['limit']
    return {'count': len(values), 'offset': offset, 'limit': limit,
            'vintage_dates' if 'vintagedates' in url else 'observations': values[offset:offset + limit]}


@pytest.fixture(autouse=True)
def deny_unmocked_provider_http(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Offline contract suite attempted provider HTTP')
    monkeypatch.setattr(requests, 'get', forbidden)


@pytest.fixture
def setup(tmp_path):
    pit = PITStore(ResearchStore(tmp_path / 'research.sqlite3'), clock=lambda: NOW)
    calls, logs = [], []
    clock = Clock()
    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return response(sec_payload(url) if 'sec.gov' in url else fred_payload(url, kwargs['params']))
    args = dict(transport=transport, limiter=RateLimiter(clock.monotonic, clock.sleep),
                sleep=clock.sleep, clock=clock.now, monotonic=clock.monotonic, log=logs.append)
    collector = Collector(pit, CONFIG, sec=SECClient(CONFIG, **args), alfred=ALFREDClient(CONFIG, **args))
    return collector, calls, logs, clock


@pytest.mark.parametrize('rate', [0, -1, 10.01, float('inf'), float('nan')])
def test_bad_rate(rate):
    with pytest.raises(ValueError):
        replace(CONFIG, sec_rate=rate)


@pytest.mark.parametrize('rate', [.1, 1, 5, 10])
def test_good_rate(rate):
    assert replace(CONFIG, sec_rate=rate).sec_rate == rate


@pytest.mark.parametrize('agent', ['', 'bot', 'nobody@foo.com', 'bot a@b', 'bot\nx@y.com', 'bot\ra@b.com'])
def test_sec_missing_identity_prevents_transport(agent):
    with patch('requests.get', side_effect=AssertionError('network')):
        with pytest.raises(ProviderError):
            SECClient(replace(CONFIG, sec_user_agent=agent)).tickers()


@pytest.mark.parametrize('key', ['', 'x', 'A' * 32, 'a' * 31, 'a' * 33, '!' * 32])
def test_fred_key_validation(key):
    with pytest.raises(ProviderError):
        replace(CONFIG, fred_api_key=key).validate('alfred')


def test_user_agent_and_rate_applied(setup):
    collector, calls, _, clock = setup
    collector.sec.tickers(); collector.sec.submissions(CIK)
    assert all(c[1]['headers']['User-Agent'] == CONFIG.sec_user_agent for c in calls)
    assert clock.waits == [.2]


def test_process_shared_limiter():
    from stock_app.research.pit.transport import SEC_LIMITER
    a, b = SECClient(CONFIG), SECClient(CONFIG)
    assert a.limiter is b.limiter is None
    clock = Clock()
    with patch.object(SEC_LIMITER, 'clock', clock.monotonic), patch.object(SEC_LIMITER, 'sleep', clock.sleep), patch.object(SEC_LIMITER, 'next_at', 0):
        with patch('requests.get', return_value=response({})):
            a.tickers(); b.tickers()
    assert clock.waits == [.2]


@pytest.mark.parametrize('status_code', [429, 500, 502, 503, 504])
def test_transient_retry_bounded(status_code):
    clock = Clock(); calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        return response({}, status_code)
    client = SECClient(CONFIG, transport=transport, limiter=RateLimiter(clock.monotonic, clock.sleep), sleep=clock.sleep)
    with pytest.raises(ProviderError):
        client.tickers()
    assert len(calls) == 3
    assert clock.waits == [1, 2]


@pytest.mark.parametrize('status_code', [301, 400, 401, 403, 404, 422, 501])
def test_permanent_response_not_retried(status_code):
    calls = []
    client = SECClient(CONFIG, transport=lambda *a, **k: calls.append(1) or response({}, status_code), limiter=RateLimiter(lambda: 1, lambda _: None))
    with pytest.raises(ProviderError):
        client.tickers()
    assert len(calls) == 1


@pytest.mark.parametrize('failure', [requests.Timeout, requests.ConnectionError])
def test_transport_retry(failure):
    clock = Clock(); calls = []
    def transport(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise failure('https://sensitive?api_key=' + KEY)
        return response({})
    client = SECClient(CONFIG, transport=transport, limiter=RateLimiter(clock.monotonic, clock.sleep), sleep=clock.sleep)
    assert len(client.tickers().attempts) == 2


@pytest.mark.parametrize('retry_after,expected', [('4', 4), ('Thu, 24 Sep 2026 12:00:05 GMT', 5), ('invalid', 1)])
def test_retry_after(retry_after, expected):
    clock = Clock(); responses = iter([response({}, 429, {'Retry-After': retry_after}), response({})])
    client = SECClient(CONFIG, transport=lambda *a, **k: next(responses), limiter=RateLimiter(clock.monotonic, clock.sleep), sleep=clock.sleep, clock=clock.now)
    client.tickers()
    assert clock.waits == [expected]


def test_long_retry_after_does_not_retry_early():
    clock = Clock()
    client = SECClient(CONFIG, transport=lambda *a, **k: response({}, 429, {'Retry-After': '3600'}), limiter=RateLimiter(clock.monotonic, clock.sleep), sleep=clock.sleep)
    with pytest.raises(ProviderError):
        client.tickers()
    assert not clock.waits


@pytest.mark.parametrize('provider,scope,expected', [('sec', SEC_SCOPE, 3), ('alfred', ALFRED_SCOPE, 2)])
def test_collection_idempotency_and_raw_link(setup, provider, scope, expected):
    c, calls, _, _ = setup
    result = c.collect(scope, execution_key='one')
    assert result['status'] == 'completed', result
    assert len(calls) == expected
    counts = {k: len(list_rows(c.store.path, k)) for k in ('sources', 'identities', 'raw', 'events', 'revisions')}
    assert c.collect(scope, execution_key='one') == result
    assert len(calls) == expected
    assert c.collect(scope, execution_key='two')['status'] == 'completed'
    assert {k: len(list_rows(c.store.path, k)) for k in counts} == counts
    for r in list_rows(c.store.path, 'revisions'):
        assert verify_raw(c.store.path, r['raw_id'])['status'] == 'verified'
    assert audit(c.store.path, depth='artifact')['status'] == 'ok'


def test_sec_identity_and_proxy(setup):
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='one')
    identities = list_rows(c.store.path, 'identities')
    assert [i['kind'] for i in identities] == ['entity']
    assert identities[0]['id'] != CIK
    assert list_rows(c.store.path, 'identifiers')[0]['namespace'] == 'cik'
    strict = get_as_of(c.store.path, as_of=NOW, strictness='strict')
    assert not strict['revisions']
    proxy = get_as_of(c.store.path, as_of=NOW, strictness='allow_proxy')
    assert proxy['revisions'][0]['availability_kind'] == 'proxy'
    manifest = input_manifest(c.store.path, as_of=NOW, strictness='allow_proxy')
    assert leakage_check(c.store.path, manifest)['status'] == 'warning'


def test_amendment_preserved_unresolved(setup):
    c, *_ = setup
    original = c.sec.transport
    def transport(url, **kwargs):
        result = original(url, **kwargs)
        if '/submissions/' in url:
            data = json.loads(result.content); data['filings']['recent'] = filing_rows(True)
            return response(data)
        return result
    c.sec.transport = transport
    assert c.collect(SEC_SCOPE, execution_key='one')['status'] == 'completed'
    assert sum(e['data_type'] == 'sec_filing' for e in list_rows(c.store.path, 'events')) == 2
    assert any(i['category'] == 'ambiguous_amendment' for i in audit(c.store.path)['issues'])


@pytest.mark.parametrize('provider,scope', [('sec', SEC_SCOPE), ('alfred', ALFRED_SCOPE)])
def test_parser_failure_retains_raw_and_reconciliation(setup, provider, scope):
    c, *_ = setup
    client = c.sec if provider == 'sec' else c.alfred
    client.transport = lambda *a, **k: response(b'{invalid')
    result = c.collect(scope, execution_key='bad')
    assert result['status'] == 'blocked'
    raws = list_rows(c.store.path, 'raw')
    assert len(raws) == 1
    assert Path(raws[0]['document']['path']).read_bytes() == b'{invalid'
    assert any(i['category'] == 'provider_parser_failure' for i in findings(c.store.path))
    assert not list_rows(c.store.path, 'revisions')


def test_changed_response_retained_without_equivalent_revision(setup):
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='one')
    before = len(list_rows(c.store.path, 'revisions'))
    original = c.sec.transport
    def transport(url, **kwargs):
        r = original(url, **kwargs)
        data = json.loads(r.content)
        data['additional_provider_field'] = 'new'
        return response(data)
    c.sec.transport = transport
    assert c.collect(SEC_SCOPE, execution_key='two')['status'] == 'completed'
    assert len(list_rows(c.store.path, 'raw')) == 6
    assert len(list_rows(c.store.path, 'revisions')) == before


def test_alfred_old_new_and_missing(setup):
    c, *_ = setup
    c.collect(ALFRED_SCOPE, execution_key='one')
    old = get_as_of(c.store.path, as_of='2025-02-15T00:00:00Z', strictness='allow_proxy')
    new = get_as_of(c.store.path, as_of='2025-03-15T00:00:00Z', strictness='allow_proxy')
    assert [r['document']['payload']['value'] for r in old['revisions']] == ['100']
    assert sorted(r['document']['payload']['value'] for r in new['revisions']) == ['.', '103']
    assert not get_as_of(c.store.path, as_of=NOW, strictness='allow_proxy')['revisions']  # No extrapolation beyond requested realtime interval.


def test_secret_never_persisted_or_logged(setup):
    c, calls, logs, _ = setup
    c.collect(ALFRED_SCOPE, execution_key='one')
    assert calls[0][1]['params']['api_key'] == KEY
    assert KEY not in repr(CONFIG)
    assert KEY not in json.dumps(logs)
    for table in ('raw', 'revisions'):
        assert KEY not in json.dumps(list_rows(c.store.path, table))
    for table in ('pit_collections', 'pit_collection_pages', 'pit_parse_results', 'pit_http_attempts'):
        assert KEY not in json.dumps(collection_rows(c.store.path, table))


def test_key_echo_refused(setup):
    c, *_ = setup
    c.alfred.transport = lambda *a, **k: response({'echo': KEY})
    assert c.collect(ALFRED_SCOPE, execution_key='one')['status'] == 'failed'
    assert not list_rows(c.store.path, 'raw')


def test_historical_request_explicit(setup):
    c, calls, *_ = setup
    c.collect(ALFRED_SCOPE, execution_key='one')
    for _, kw in calls:
        assert kw['params']['realtime_start'] == ALFRED_SCOPE.start
        assert kw['params']['realtime_end'] == ALFRED_SCOPE.end
    c.alfred.page('GDP', start='2025-02-01', end='2025-02-01', observation_start='2025-01-01', observation_end='2025-03-31', vintage='2025-02-01')
    assert calls[-1][1]['params']['vintage_dates'] == '2025-02-01'
    assert 'realtime_start' not in calls[-1][1]['params']


def test_pagination_crash_resume_no_repeated_page(setup):
    c, calls, *_ = setup
    scope = replace(ALFRED_SCOPE, page_size=1)
    original = c.alfred.transport
    failed = [False]
    def transport(url, **kwargs):
        if kwargs['params']['offset'] == 1 and not failed[0]:
            failed[0] = True
            return response({}, 400)
        return original(url, **kwargs)
    c.alfred.transport = transport
    assert c.collect(scope, execution_key='resume')['status'] == 'failed'
    assert len(collection_rows(c.store.path, 'pit_collection_pages')) == 1
    assert c.collect(scope, execution_key='resume')['status'] == 'completed'
    assert len(collection_rows(c.store.path, 'pit_collection_pages')) == 5
    assert len(calls) == 5
    assert not findings(c.store.path)


def test_page_budget_is_explicit_incomplete(setup):
    c, *_ = setup
    result = c.collect(replace(ALFRED_SCOPE, page_size=1, max_pages=1), execution_key='one')
    assert result['status'] == 'blocked' and not result['complete']
    assert findings(c.store.path)


@pytest.mark.parametrize('change', [{'start': ''}, {'end': ''}, {'start': '2026-01-01'}, {'end': '2028-01-01'}, {'max_pages': 0}, {'max_pages': 31}, {'page_size': 0}, {'page_size': 1001}, {'symbol': 'MSFT'}, {'forms': ()}, {'forms': ('UNKNOWN',)}])
def test_invalid_bounds(change):
    with pytest.raises((ValueError, TypeError)):
        replace(SEC_SCOPE, **change)


@pytest.mark.parametrize('scope', [SEC_SCOPE, ALFRED_SCOPE])
def test_dry_run_zero_network_and_no_records(setup, scope):
    c, calls, *_ = setup
    plan = c.collect(scope, execution_key='plan', dry_run=True)
    assert not plan['network_calls'] and not calls
    assert not collection_rows(c.store.path, 'pit_collections')
    assert json.loads(json.dumps(plan))['max_pages'] == 12


@pytest.mark.parametrize('scope', [SEC_SCOPE, ALFRED_SCOPE])
def test_offline_reparse_deterministic(setup, scope):
    c, calls, *_ = setup
    c.collect(scope, execution_key='one')
    before = list_rows(c.store.path, 'revisions')
    with patch.object(c.sec, 'transport', side_effect=AssertionError('network')), patch.object(c.alfred, 'transport', side_effect=AssertionError('network')):
        for raw in list_rows(c.store.path, 'raw'):
            result = c.reparse(raw['id'])
            assert result['status'] == 'completed'
            assert not result['network_used']
    assert list_rows(c.store.path, 'revisions') == before


def test_checksum_failure_surfaces(setup):
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='one')
    raw = list_rows(c.store.path, 'raw')[0]
    path = Path(raw['document']['path']); path.chmod(0o600); path.write_bytes(b'changed')
    assert verify_raw(c.store.path, raw['id'])['status'] == 'mismatch'
    assert c.reparse(raw['id'])['status'] == 'blocked'
    assert audit(c.store.path, depth='artifact')['status'] == 'attention'


@pytest.mark.parametrize('scope', [SEC_SCOPE, ALFRED_SCOPE])
def test_concurrent_collection_sql_uniqueness(setup, scope):
    c, calls, *_ = setup
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: c.collect(scope, execution_key='same'), range(3)))
    assert all(r['status'] == 'completed' for r in results)
    assert len(collection_rows(c.store.path, 'pit_collections')) == 1
    assert len(calls) == (3 if scope.provider == 'sec' else 2)
    assert audit(c.store.path)['status'] == 'ok'


def test_query_plans_indexed(setup):
    c, *_ = setup
    with c.store.connection() as db:
        for sql, args in [
            ('SELECT * FROM pit_raw WHERE source_id=? AND sha256=? AND json_extract(document,\'$.resource\')=?', ('x', 'y', 'z')),
            ('SELECT * FROM pit_revisions WHERE event_id=? AND available_bound<=?', ('x', NOW)),
            ('SELECT * FROM pit_identifiers WHERE namespace=? AND value=?', ('cik', CIK)),
            ('SELECT * FROM pit_collection_pages WHERE collection_id=? AND page_key=?', ('x', 'y'))]:
            plan = ' '.join(str(tuple(r)) for r in db.execute('EXPLAIN QUERY PLAN ' + sql, args))
            assert 'SEARCH' in plan and 'INDEX' in plan


def test_migration_preserves_all_tables(setup):
    from stock_app.research.pit.collection_migration import inventory, migrate
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='one')
    with c.store.connection() as db:
        before = inventory(db)
    for _ in range(2):
        result = migrate(c.store.path)
        assert result['before'] == result['after'] == before
        assert Path(result['backup']).is_file()


def test_registration_opt_in(setup, monkeypatch):
    from stock_app.jobs.service import registry
    c, *_ = setup
    monkeypatch.delenv('PIT_ENABLE_REFRESH', raising=False)
    assert not any(j.kind == 'pit-refresh' for j in registry(c.store))
    monkeypatch.setenv('PIT_ENABLE_REFRESH', '1')
    assert len([j for j in registry(c.store) if j.kind == 'pit-refresh']) == 2


def test_operator_job_and_no_retraining(setup):
    from stock_app.research.pit.collection_jobs import run_operator
    c, *_ = setup
    with patch('stock_app.research.experiments.run_experiment', side_effect=AssertionError('training')):
        result = run_operator(c, SEC_SCOPE, 'one')
    assert result['status'] == 'succeeded'
    assert run_operator(c, SEC_SCOPE, 'one')['id'] == result['id']
    assert not c.store.list('models') and not c.store.list('forecasts')
    with c.store.connection() as db:
        assert not db.execute('SELECT * FROM active_models').fetchall()


@pytest.mark.parametrize('command', ['provider-status', 'coverage', 'sources', 'audit'])
def test_status_cli(setup, capsys, command):
    c, calls, *_ = setup
    assert main(['--database', str(c.store.path), command]) == 0
    json.loads(capsys.readouterr().out)
    assert not calls


@pytest.mark.parametrize('provider', ['sec', 'alfred'])
def test_backfill_cli_plan(setup, capsys, provider):
    c, calls, *_ = setup
    args = ['--database', str(c.store.path), 'backfill', provider, '--start', '2025-01-01', '--end', '2025-03-31', '--dry-run']
    if provider == 'alfred':
        args += ['--observation-start', '2025-01-01', '--observation-end', '2025-03-31']
    assert main(args) == 0
    assert not json.loads(capsys.readouterr().out)['network_calls'] and not calls


def test_freshness_coverage_distinct(setup):
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='one')
    result = status(c.store.path, CONFIG)
    assert result['latest_successful_observation']['sec'] == NOW
    assert coverage(c.store.path)['SEC']['filing_date_range'] == ['2025-02-01', '2025-02-01']
    assert coverage(c.store.path)['SEC']['securities_mapped'] == 0


def test_identity_ambiguity_blocked(setup):
    c, *_ = setup
    c.sec.transport = lambda *a, **k: response({'0': {'ticker': 'AAPL', 'cik_str': 1, 'title': 'one'}, '1': {'ticker': 'AAPL', 'cik_str': 2, 'title': 'two'}})
    assert c.collect(SEC_SCOPE, execution_key='one')['status'] == 'blocked'
    assert not list_rows(c.store.path, 'identities')


def test_unsupported_xbrl_retained(setup):
    c, *_ = setup
    original = c.sec.transport
    def transport(url, **kwargs):
        r = original(url, **kwargs)
        if 'companyconcept' in url:
            data = json.loads(r.content); data['units']['USD'][0]['dimensions'] = {'segment': 'unsupported'}
            return response(data)
        return r
    c.sec.transport = transport
    assert c.collect(SEC_SCOPE, execution_key='one')['status'] == 'completed'
    assert coverage(c.store.path)['SEC']['xbrl_facts'] == 0
    assert any(r['document'].get('unsupported') == 1 for r in collection_rows(c.store.path, 'pit_parse_results'))


@pytest.mark.parametrize('endpoint', ['provider-status', 'coverage', 'raw'])
def test_read_only_api(setup, monkeypatch, endpoint):
    from flask import Flask
    from stock_app.research.api import api
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='api')
    if endpoint == 'raw':
        endpoint += '/' + list_rows(c.store.path, 'raw')[0]['id']
    monkeypatch.setenv('STOCK_RESEARCH_ROOT', str(c.store.path.parent))
    app = Flask(__name__); app.register_blueprint(api)
    with patch('stock_app.research.api.runtime', side_effect=AssertionError('runtime')):
        assert app.test_client().get('/api/research/pit/' + endpoint).status_code == 200
        assert app.test_client().post('/api/research/pit/' + endpoint).status_code == 405
    assert app.test_client().post('/api/research/pit/collect/sec').status_code == 404


@pytest.mark.parametrize('action', ['show', 'verify', 'reparse'])
def test_raw_cli(setup, capsys, action):
    c, *_ = setup
    c.collect(SEC_SCOPE, execution_key='raw')
    raw = list_rows(c.store.path, 'raw')[0]
    with patch('requests.get', side_effect=AssertionError('network')):
        assert main(['--database', str(c.store.path), 'raw', action, raw['id']]) == 0
    assert json.loads(capsys.readouterr().out)


@pytest.mark.parametrize('provider', ['sec', 'alfred'])
def test_collect_cli_uses_durable_runner(setup, monkeypatch, capsys, provider):
    c, *_ = setup
    monkeypatch.setenv('SEC_USER_AGENT', CONFIG.sec_user_agent)
    monkeypatch.setenv('FRED_API_KEY', CONFIG.fred_api_key)
    args = ['--database', str(c.store.path), 'collect', provider, '--start', '2025-01-01', '--end', '2025-03-31', '--execution-key', 'cli']
    if provider == 'alfred':
        args += ['--series', 'GDP', '--observation-start', '2025-01-01', '--observation-end', '2025-03-31']
    with patch('stock_app.research.pit.collection.Collector', return_value=c):
        assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'succeeded'


def test_reconciliation_materializes_parser_case(setup):
    from stock_app.research.integrity_reconcile import detect_cases
    from stock_app.jobs.store import JobStore
    c, *_ = setup
    JobStore(c.store)
    c.sec.transport = lambda *a, **k: response(b'bad')
    c.collect(SEC_SCOPE, execution_key='case')
    cases = detect_cases(c.store)
    assert any(r['category'] == 'pit_provider_parser_failure' and r['state'] == 'open' for r in cases)


def test_sec_historical_page_accession_dedup(setup):
    c, calls, *_ = setup
    original = c.sec.transport
    def transport(url, **kwargs):
        if '-submissions-' in url:
            calls.append((url, kwargs))
            return response(filing_rows())
        result = original(url, **kwargs)
        if '/submissions/' in url:
            data = json.loads(result.content)
            data['filings']['files'] = [{'name': 'CIK' + CIK + '-submissions-001.json', 'filingFrom': '2025-01-01', 'filingTo': '2025-03-31'}]
            return response(data)
        return result
    c.sec.transport = transport
    assert c.collect(SEC_SCOPE, execution_key='one')['status'] == 'completed'
    assert len(calls) == 4
    assert coverage(c.store.path)['SEC']['filings'] == 1
    assert len(list_rows(c.store.path, 'revisions')) == 2


@pytest.mark.parametrize('field,value', [('offset', 1), ('count', 999), ('limit', 3), ('observations', {}), ('count', '3')])
def test_alfred_contract_drift_no_partial_records(setup, field, value):
    c, *_ = setup
    original = c.alfred.transport
    def transport(url, **kwargs):
        result = original(url, **kwargs)
        if 'observations' in url:
            data = json.loads(result.content); data[field] = value
            return response(data)
        return result
    c.alfred.transport = transport
    assert c.collect(ALFRED_SCOPE, execution_key='drift')['status'] == 'blocked'
    assert len(list_rows(c.store.path, 'raw')) == 2
    assert not list_rows(c.store.path, 'revisions')


def test_sec_contract_drift_no_partial_records(setup):
    c, *_ = setup
    original = c.sec.transport
    def transport(url, **kwargs):
        result = original(url, **kwargs)
        if '/submissions/' in url:
            data = json.loads(result.content); data['filings']['recent']['form'] = []
            return response(data)
        return result
    c.sec.transport = transport
    assert c.collect(SEC_SCOPE, execution_key='drift')['status'] == 'blocked'
    assert not list_rows(c.store.path, 'revisions')


def test_no_default_network_and_environment_isolation(setup, monkeypatch):
    c, *_ = setup
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    monkeypatch.delenv('FRED_API_KEY', raising=False)
    monkeypatch.setenv('PIT_ALFRED_SERIES', 'GDP')
    config = Config.environment()
    assert config.series == ('GDP',)
    with patch('socket.socket', side_effect=AssertionError('network')):
        assert status(c.store.path, config)['configuration'] == {'sec': 'missing_or_invalid', 'alfred': 'missing_or_invalid'}
        assert not coverage(c.store.path)['raw_payloads']


def test_collection_preserves_models_bindings_and_old_history(setup, tmp_path):
    from stock_app.research.pit.collection_migration import inventory
    c, *_ = setup
    model = tmp_path / 'model.bin'; model.write_bytes(b'unchanged model')
    c.store.put('models', {'id': 'legacy-model', 'artifact': str(model)})
    c.store.put('forecasts', {'id': 'legacy-forecast', 'value': 42})
    with c.store.connection() as db:
        db.execute('INSERT INTO active_models VALUES(?,?,?)', ('AAPL', 'binary', 'legacy-model'))
    with c.store.connection() as db:
        before = inventory(db)
    c.collect(SEC_SCOPE, execution_key='preserve')
    c.collect(ALFRED_SCOPE, execution_key='preserve')
    with c.store.connection() as db:
        after = inventory(db)
    assert before['models'] == after['models']
    for table in before['tables']:
        if not table.startswith('pit_'):
            assert before['tables'][table] == after['tables'][table]


def test_resume_after_raw_parse_crash(setup):
    c, calls, *_ = setup
    original = c._reparse
    with patch.object(c, '_reparse', side_effect=RuntimeError('crash after checkpoint')):
        assert c.collect(SEC_SCOPE, execution_key='crash')['status'] == 'failed'
    assert len(calls) == 1
    assert c.collect(SEC_SCOPE, execution_key='crash')['status'] == 'completed'
    assert len(calls) == 3


def test_resume_durable_operator_preserves_blocked_history(setup):
    from stock_app.research.pit.collection_jobs import run_operator
    c, *_ = setup
    with patch.object(c.sec, 'transport', return_value=response({}, 400)):
        first = run_operator(c, SEC_SCOPE, 'resume')
    assert first['status'] == 'blocked'
    assert run_operator(c, SEC_SCOPE, 'resume')['id'] == first['id']
    # A later explicit continuation gets a new operational attempt, same collection key.
    c.pit.clock = lambda: '2026-09-24T12:01:00+00:00'
    second = run_operator(c, SEC_SCOPE, 'resume', resume=True)
    assert second['status'] == 'succeeded' and second['id'] != first['id']
    from stock_app.jobs.store import JobStore
    assert JobStore(c.store).get(first['id'])['status'] == 'blocked'


def test_scheduled_refresh_pins_scope_and_has_no_training(setup, monkeypatch):
    from stock_app.jobs.core import JobRunner
    from stock_app.research.pit.collection_jobs import definitions, refresh_handler
    c, calls, *_ = setup
    monkeypatch.setenv('SEC_USER_AGENT', CONFIG.sec_user_agent)
    monkeypatch.setenv('FRED_API_KEY', CONFIG.fred_api_key)
    runtime = SimpleNamespace(store=c.store)
    runner = JobRunner(runtime, definitions(), {'pit-refresh': refresh_handler(c.store, lambda: datetime.fromisoformat(NOW))}, clock=lambda: NOW)
    run = runner.enqueue(definitions()[0], '2025-03-31T20:30:00Z')
    with patch('stock_app.research.pit.collection_jobs.Collector', return_value=c):
        result = runner._execute(run['id'])
        assert result['status'] == 'succeeded'
        assert runner._execute(run['id'])['status'] == 'succeeded'
    assert len(calls) == 3
    collection = collection_rows(c.store.path, 'pit_collections')[0]['document']
    assert collection['scope']['end'] == '2025-03-31'
    assert collection['scope']['mode'] == 'refresh'
    assert collection['execution_key'] == run['id']


def test_retry_keeps_collection_and_operational_identity(setup):
    from stock_app.research.pit.collection_jobs import run_operator
    c, calls, logs, clock = setup
    original = c.sec.transport
    attempts = [0]
    def transport(url, **kwargs):
        attempts[0] += 1
        if attempts[0] == 1:
            return response({}, 429, {'Retry-After': '2'})
        return original(url, **kwargs)
    c.sec.transport = transport
    assert run_operator(c, SEC_SCOPE, 'retry')['status'] == 'succeeded'
    history = collection_rows(c.store.path, 'pit_http_attempts')
    assert len({r['collection_id'] for r in history}) == 1
    assert history[0]['document']['status'] == 429
    assert history[1]['document']['attempt'] == 2
    assert len(collection_rows(c.store.path, 'pit_collections')) == 1


def test_unknown_provider_fails_before_transport():
    transport = Mock(return_value=response({}))
    client = ALFREDClient(CONFIG, transport=transport)
    with pytest.raises(ProviderError, match='Unsupported provider'):
        client.fetch('unknown', '/series/observations')
    transport.assert_not_called()


@pytest.mark.parametrize('status_code,body,headers', [
    (200, {}, {}), (400, {}, {}), (429, {}, {}),
    (503, {}, {}), (429, {}, {'Retry-After': '3600'}),
    (200, {'echo': KEY}, {}),
])
def test_response_closed_on_success_rejection_and_retry(status_code, body, headers):
    responses = []
    clock = Clock()
    def transport(*args, **kwargs):
        item = response(body, status_code, headers)
        responses.append(item)
        return item
    def sleep(delay):
        assert all(item.close.call_count == 1 for item in responses)
        clock.sleep(delay)
    client = ALFREDClient(CONFIG, transport=transport,
                          limiter=RateLimiter(clock.monotonic, clock.sleep), sleep=sleep)
    if status_code == 200 and not body:
        client.fetch('alfred', '/series/observations')
    else:
        with pytest.raises(ProviderError):
            client.fetch('alfred', '/series/observations')
    assert responses
    for item in responses:
        item.close.assert_called_once_with()
