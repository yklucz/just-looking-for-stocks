"""Bounded evidence-first collections, durable page resume and offline reparse."""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date
from hashlib import sha256
import fcntl
import json
from pathlib import Path
import threading

from ..forecast_identity import canonical_json, digest
from ..integrity import reader, rows
from . import live_parsers as parsers
from .providers import source, sec_filing, sec_xbrl, alfred_observation
from .store import get, list_rows, resolve_identifier
from .temporal import day, instant
from .transport import Config, SECClient, ALFREDClient

LOCK = threading.RLock()


@contextmanager
def collection_lock(path):
    # One evidence/normalization writer at a time across processes and threads.
    with LOCK, open(Path(path).parent / 'pit-collection.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@dataclass(frozen=True)
class Scope:
    provider: str
    start: str
    end: str
    symbol: str = 'AAPL'
    series: tuple = ('GDP', 'DGS10')
    forms: tuple = ('10-K', '10-Q', '10-K/A', '10-Q/A')
    observation_start: str = ''
    observation_end: str = ''
    page_size: int = 100
    max_pages: int = 12
    xbrl: bool = True
    mode: str = 'backfill'

    def __post_init__(self):
        if self.provider not in {'sec', 'alfred'} or self.mode not in {'refresh', 'backfill'}:
            raise ValueError('Explicit provider and mode required')
        if day(self.start) > day(self.end) or (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days > 366:
            raise ValueError('Pilot collection window must be bounded to at most 366 days')
        if not 1 <= self.max_pages <= 30 or not 1 <= self.page_size <= 1000:
            raise ValueError('Bounded page budget required')
        if self.provider == 'sec' and (self.symbol != 'AAPL' or not self.forms or set(self.forms) - {'10-K', '10-Q', '10-K/A', '10-Q/A', '8-K', '8-K/A'}):
            raise ValueError('SEC pilot symbol/form allowlist violated')
        if self.provider == 'alfred':
            if not self.series or len(self.series) > 3 or len(set(self.series)) != len(self.series):
                raise ValueError('Explicit small series allowlist required')
            if day(self.observation_start) > day(self.observation_end) or (date.fromisoformat(self.observation_end) - date.fromisoformat(self.observation_start)).days > 366:
                raise ValueError('Bounded observation window required')

    def plan(self, config):
        if self.provider == 'alfred' and not set(self.series) <= set(config.series):
            raise ValueError('Series outside configured allowlist')
        return {'status': 'planned', 'provider': self.provider, 'scope': json.loads(canonical_json(asdict(self))), 'network_calls': False,
                'max_pages': self.max_pages, 'rate': config.sec_rate if self.provider == 'sec' else 2,
                'scope_completeness': 'Only declared pilot window; no global completeness claim'}


def collection_rows(path, table):
    from .collection_schema import TABLES
    if table not in TABLES:
        raise ValueError('Unknown collection table')
    with reader(path) as db:
        return rows(db, table)


def verify_raw(path, raw_id):
    raw = get(path, 'raw', raw_id)
    try:
        valid = sha256(Path(raw['document']['path']).read_bytes()).hexdigest() == raw['sha256']
    except OSError:
        valid = False
    return {'raw_id': raw_id, 'status': 'verified' if valid else 'mismatch', 'network_used': False}


class BoundEvidence:
    """Reuse Phase 2A typed adapters while linking directly to full provider bytes.

    Adapters receive deterministic extracted rows, but raw() always returns the
    retained parent envelope. No synthesized JSON masquerades as provider evidence.
    """
    def __init__(self, pit, raw):
        self.pit, self.raw_record, self.store = pit, raw, pit.store
        self.created = self.reused = 0

    def __getattr__(self, name):
        return getattr(self.pit, name)

    def raw(self, **_):
        return self.raw_record

    def revision(self, **kwargs):
        # Equivalent facts from changed envelopes reuse their earliest provenance.
        # Later retrievals and parser results still point to every response.
        with self.store.connection() as db:
            candidates = db.execute('SELECT id,document FROM pit_revisions WHERE event_id=?', (kwargs['event_id'],)).fetchall()
        expected = {k: kwargs.get(k) for k in ('source_record_id', 'payload', 'event_time', 'event_end', 'source_time', 'supersedes')}
        expected['availability'] = kwargs['availability'].document()
        expected['relations'] = kwargs.get('relations') or {}
        for row in candidates:
            doc = json.loads(row['document'])
            if all(doc.get(k) == v for k, v in expected.items()):
                self.reused += 1
                return get(self.store.path, 'revisions', row['id'])
        self.created += 1
        return self.pit.revision(**kwargs)


class Collector:
    def __init__(self, pit, config=None, *, sec=None, alfred=None):
        self.pit, self.store = pit, pit.store
        self.config = config or Config.environment()
        self.sec = sec or SECClient(self.config)
        self.alfred = alfred or ALFREDClient(self.config)

    def _state(self, identity, provider, status, doc):
        with self.store.connection() as db:
            db.execute('INSERT INTO pit_collections VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,document=excluded.document',
                       (identity, provider, status, canonical_json(doc)))

    def _entity(self, raw, identity):
        resolved = resolve_identifier(self.store.path, identity['cik'], namespace='cik')
        if resolved['status'] == 'ambiguous':
            raise parsers.ContractError('Ambiguous SEC entity mapping')
        if resolved['status'] == 'resolved':
            return resolved['identity_id']
        entity = self.pit.identity(kind='entity', key='sec-issuer:' + identity['cik'], name=identity['name'],
                                   evidence={'raw_id': raw['id'], 'basis': 'SEC current ticker/CIK association; no security or historical period claim'})
        self.pit.identifier(identity_id=entity['id'], source_id=raw['source_id'], namespace='cik', value=identity['cik'],
                            raw_id=raw['id'], evidence={'basis': 'SEC CIK issuer identity'})
        # Ticker describes current issuer association only; never create a security from CIK.
        return entity['id']

    def _retain(self, response, kind, context):
        feed = 'sec-filings' if kind in {'sec-submissions', 'sec-history'} else kind
        src = source(self.pit, 'SEC' if response.provider == 'sec' else 'FRED_ALFRED', feed)
        checksum = sha256(response.body).hexdigest()
        with self.store.connection() as db:
            prior = db.execute("SELECT id FROM pit_raw WHERE source_id=? AND sha256=? AND json_extract(document,'$.resource')=? ORDER BY observed_time LIMIT 1",
                               (src['id'], checksum, response.resource)).fetchone()
        if prior:
            return get(self.store.path, 'raw', prior['id'])
        return self.pit.raw(source_id=src['id'], resource=response.resource, payload=response.body,
                            observed_time=response.observed_time, parser_version=parsers.VERSION,
                            metadata={'http_status': response.status, 'headers': response.headers, 'kind': kind, 'context': context})

    def reparse(self, raw_id, *, context=None, parser_version=parsers.VERSION):
        with collection_lock(self.store.path):
            return self._reparse(raw_id, context=context, parser_version=parser_version)

    def _reparse(self, raw_id, *, context=None, parser_version=parsers.VERSION):
        if parser_version != parsers.VERSION:
            raise ValueError('Unsupported parser version')
        raw = get(self.store.path, 'raw', raw_id)
        metadata = raw['document']['metadata']
        context = context or metadata['context']
        scope_key = digest(context)
        result = {'raw_id': raw_id, 'parser_version': parser_version, 'context': context, 'network_used': False}
        try:
            if verify_raw(self.store.path, raw_id)['status'] != 'verified':
                raise parsers.ContractError('Raw checksum mismatch')
            body = Path(raw['document']['path']).read_bytes()
            kind = metadata['kind']
            bound = BoundEvidence(self.pit, raw)
            scope = context['scope']
            if kind == 'sec-tickers':
                identity = parsers.ticker_identity(body, scope['symbol'])
                result.update(identity, entity_id=self._entity(raw, identity), records=[], revisions=[])
            elif kind in {'sec-submissions', 'sec-history', 'sec-assets'}:
                if kind == 'sec-assets':
                    parsed = parsers.assets(body, scope, context['cik'])
                    adapter = sec_xbrl
                else:
                    parsed = parsers.submissions(body, scope, context['cik'], historical=kind == 'sec-history')
                    adapter = sec_filing
                result.update(parsed)
                result['revisions'] = [adapter(bound, entity_id=context['entity_id'], source_id=raw['source_id'],
                    resource=raw['document']['resource'], payload=canonical_json(r).encode(), observed_time=raw['observed_time'])['id']
                    for r in parsed['records']]
            elif kind in {'alfred-vintages', 'alfred-observations'}:
                parsed = parsers.alfred(body, scope, context['series'], context['offset'], discovery=kind == 'alfred-vintages')
                result.update(parsed)
                result['revisions'] = [] if kind == 'alfred-vintages' else [alfred_observation(bound,
                    source_id=raw['source_id'], resource=raw['document']['resource'], payload=canonical_json(r).encode(),
                    observed_time=raw['observed_time'], date_timezone='America/Chicago')['id'] for r in parsed['records']]
            else:
                raise parsers.ContractError('Unknown retained provider contract')
            result.update(status='completed', revisions_created=bound.created, revisions_reused=bound.reused)
        except Exception as exc:
            # Persist a safe category only; response/exception strings may contain provider secrets.
            result.update(status='blocked', error_class=type(exc).__name__, reason=str(exc) if isinstance(exc, parsers.ContractError) else 'Parser, checksum or identity contract requires review')
        # History is append-only, including failures and successful retries after updates.
        result['parsed_at'] = instant(self.pit.clock())
        from uuid import uuid4
        with self.store.connection() as db:
            db.execute('INSERT INTO pit_parse_results VALUES(?,?,?,?,?,?)',
                       ('parse-' + uuid4().hex, raw_id, parser_version, scope_key, result['status'], canonical_json(result)))
        return result

    def collect(self, scope, *, execution_key, dry_run=False):
        plan = scope.plan(self.config)
        if dry_run:
            return plan
        self.config.validate(scope.provider)  # Before any effects or request.
        if not execution_key or len(execution_key) > 128:
            raise ValueError('Stable bounded execution key required')
        if scope.end > instant(self.pit.clock())[:10]:
            raise ValueError('Future collection window rejected')
        with collection_lock(self.store.path):
            return self._collect(scope, execution_key, plan)

    def _collect(self, scope, execution_key, plan):
        identity = 'collect-' + digest([asdict(scope), execution_key])
        existing = next((r for r in collection_rows(self.store.path, 'pit_collections') if r['id'] == identity), None)
        if existing and existing['status'] == 'completed':
            return existing['document']
        doc = existing['document'] if existing else dict(plan, id=identity, execution_key=execution_key, pages=0,
                                                         started_at=instant(self.pit.clock()))
        self._state(identity, scope.provider, 'running', dict(doc, status='running'))
        client = self.sec if scope.provider == 'sec' else self.alfred
        old_log = client.log

        def log(record):
            safe = dict(record, collection_id=identity)
            with self.store.connection() as db:
                db.execute('INSERT INTO pit_http_attempts(collection_id,document) VALUES(?,?)', (identity, canonical_json(safe)))
            old_log(safe)
        client.log = log
        page_count = 0

        def page(key, kind, context, fetch):
            nonlocal page_count
            page_count += 1
            if page_count > scope.max_pages:
                raise parsers.ContractError('Page budget exceeded; incomplete collection')
            with self.store.connection() as db:
                saved = db.execute('SELECT raw_id,document FROM pit_collection_pages WHERE collection_id=? AND page_key=?', (identity, key)).fetchone()
            if saved:
                raw_id = saved['raw_id']
                if verify_raw(self.store.path, raw_id)['status'] != 'verified':
                    raise parsers.ContractError('Checkpoint checksum mismatch')
                response_time = json.loads(saved['document'])['observed_time']
            else:
                doc['network_calls'] = True
                response = fetch()
                raw = self._retain(response, kind, context)
                raw_id, response_time = raw['id'], response.observed_time
                with self.store.connection() as db:
                    db.execute('INSERT INTO pit_collection_pages VALUES(?,?,?,?)',
                        (identity, key, raw_id, canonical_json({'observed_time': response_time, 'context': context})))
            old_log({'provider': scope.provider, 'resource_class': kind, 'collection_id': identity, 'raw_id': raw_id})
            parsed = self._reparse(raw_id, context=context)
            if parsed['status'] != 'completed':
                raise parsers.ContractError('Retained page requires parser/identity review')
            doc.update(pages=page_count, last_observed_at=response_time)
            self._state(identity, scope.provider, 'running', dict(doc, status='running'))
            return parsed

        try:
            context = {'scope': asdict(scope)}
            if scope.provider == 'sec':
                mapping = page('identity', 'sec-tickers', context, self.sec.tickers)
                context = dict(context, cik=mapping['cik'], entity_id=mapping['entity_id'])
                recent = page('submissions', 'sec-submissions', context, lambda: self.sec.submissions(mapping['cik']))
                for name in recent['pages']:
                    page(name, 'sec-history', context, lambda name=name: self.sec.submissions(mapping['cik'], name))
                if scope.xbrl:
                    page('assets', 'sec-assets', context, lambda: self.sec.assets(mapping['cik']))
            else:
                for series in scope.series:
                    for discovery in (True, False):
                        offset, total = 0, None
                        while True:
                            kind = 'alfred-vintages' if discovery else 'alfred-observations'
                            context = {'scope': asdict(scope), 'series': series, 'offset': offset}
                            parsed = page(f'{series}:{kind}:{offset}', kind, context,
                                lambda: self.alfred.page(series, start=scope.start, end=scope.end, offset=offset,
                                    limit=scope.page_size, observation_start=scope.observation_start,
                                    observation_end=scope.observation_end, discovery=discovery))
                            if total is not None and total != parsed['count']:
                                raise parsers.ContractError('Provider pagination changed during collection')
                            total = parsed['count']
                            if parsed['complete']:
                                break
                            offset = parsed['next_offset']
            doc.pop('error_class', None); doc.pop('reason', None)
            doc.update(status='completed', complete=True, finished_at=instant(self.pit.clock()))
        except Exception as exc:
            doc.update(status='blocked' if isinstance(exc, parsers.ContractError) else 'failed', complete=False,
                       error_class=type(exc).__name__, reason='Incomplete collection; resume retained pages using the same scope/execution key')
        finally:
            client.log = old_log
        self._state(identity, scope.provider, doc['status'], doc)
        return doc


def findings(path):
    result = []
    for row in collection_rows(path, 'pit_collections'):
        if row['status'] != 'completed':
            result.append({'category': 'incomplete_collection', 'id': row['id'], 'severity': 'warning',
                           'reason': 'Collection is ' + row['status'] + '; retained pages can be resumed'})
    latest = {}
    for row in collection_rows(path, 'pit_parse_results'):
        latest[(row['raw_id'], row['scope_key'])] = row
    for row in latest.values():
        if row['status'] != 'completed':
            result.append({'category': 'provider_parser_failure', 'id': row['raw_id'], 'severity': 'error',
                           'reason': row['document']['reason']})
    return result


def status(path, config=None):
    config = config or Config.environment()
    collections = collection_rows(path, 'pit_collections')
    attempts = collection_rows(path, 'pit_http_attempts')
    parses = collection_rows(path, 'pit_parse_results')
    configuration = {}
    for provider in ('sec', 'alfred'):
        try:
            config.validate(provider)
            configuration[provider] = 'configured'
        except ValueError:
            configuration[provider] = 'missing_or_invalid'
        except RuntimeError:
            configuration[provider] = 'missing_or_invalid'
    return {'configuration': configuration, 'sec_rate': config.sec_rate, 'series_allowlist': config.series,
            'collections': collections, 'latest_successful_observation': {
                p: max((r['document']['last_observed_at'] for r in collections if r['provider'] == p and r['status'] == 'completed'), default=None)
                for p in ('sec', 'alfred')},
            'metrics': {'raw_payloads_stored': len(list_rows(path, 'raw')), 'events_stored': len(list_rows(path, 'events')),
                        'reconciliation_findings': len(findings(path)), 'requests': len(attempts), 'successful_requests': sum(r['document']['status'] == 200 for r in attempts),
                        'retries': sum(r['document']['attempt'] > 1 for r in attempts),
                        'rate_limit_responses': sum(r['document']['status'] == 429 for r in attempts),
                        'parser_failures': sum(r['status'] != 'completed' for r in parses),
                        'revisions_created': sum(r['document'].get('revisions_created', 0) for r in parses),
                        'revisions_reused': sum(r['document'].get('revisions_reused', 0) for r in parses)},
            'network_used': False}


def coverage(path):
    events, revisions = list_rows(path, 'events'), list_rows(path, 'revisions')
    sec_sources = {r['id'] for r in list_rows(path, 'sources') if r['provider'] == 'SEC'}
    sec_entities = {r['identity_id'] for r in list_rows(path, 'identifiers') if r['namespace'] == 'cik' and r['source_id'] in sec_sources}
    securities = [r for r in list_rows(path, 'identities') if r['kind'] == 'security' and r['parent_id'] in sec_entities]
    types = {e['id']: e['data_type'] for e in events}
    filings = [r['document']['payload'] for r in revisions if types[r['event_id']] == 'sec_filing']
    macros = [r['document']['payload'] for r in revisions if types[r['event_id']] == 'macro_observation']
    def extent(values):
        return [min(values), max(values)] if values else None
    return {'scope': 'Stored pilot evidence only; not a complete archive',
            'SEC': {'entities': len(sec_entities),
                    'securities_mapped': len(securities), 'unresolved_security_mappings': len(sec_entities - {r['parent_id'] for r in securities}), 'mapping_policy': 'issuer only; security requires additional class evidence',
                    'filings': sum(e['data_type'] == 'sec_filing' for e in events),
                    'filing_date_range': extent([r['filing_date'] for r in filings]),
                    'xbrl_facts': sum(e['data_type'] == 'sec_xbrl' for e in events)},
            'ALFRED': {'series': sorted({r['series_id'] for r in macros}),
                       'observations': sum(e['data_type'] == 'macro_observation' for e in events),
                       'revisions': len(macros), 'vintage_range': extent([r['realtime_start'] for r in macros])},
            'latest_successful_collection': {p: max((r['document'].get('last_observed_at', '') for r in collection_rows(path, 'pit_collections') if r['provider'] == p and r['status'] == 'completed'), default=None) for p in ('sec', 'alfred')},
            'raw_payloads': len(list_rows(path, 'raw')), 'findings': findings(path), 'network_used': False}
