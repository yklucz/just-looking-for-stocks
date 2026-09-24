"""Credential-contained, evidence-ready provider HTTP boundary. No storage imports."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
import os
import re
import threading
import time
from urllib.parse import urlencode

import requests

LOG = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """Safe error with no URL, response body or underlying credential-bearing text."""


@dataclass(frozen=True)
class Config:
    sec_user_agent: str = field(default='', repr=False)
    fred_api_key: str = field(default='', repr=False)
    sec_rate: float = 5.0
    series: tuple = ('GDP', 'DGS10')
    symbols: tuple = ('AAPL',)
    timeout: float = 30.0
    attempts: int = 3

    def __post_init__(self):
        if not 0 < self.sec_rate <= 10:
            raise ValueError('SEC rate must be positive and at most 10 requests/second')
        if not 0 < self.timeout <= 120 or not 1 <= self.attempts <= 5:
            raise ValueError('Invalid bounded transport policy')
        if not self.series or len(self.series) > 3 or any(not re.fullmatch(r'[A-Z0-9_]+', s) for s in self.series):
            raise ValueError('Provide one to three explicit ALFRED series')
        if self.symbols != ('AAPL',):
            raise ValueError('SEC pilot allowlist is AAPL only')

    @classmethod
    def environment(cls):
        return cls(sec_user_agent=os.environ.get('SEC_USER_AGENT', ''),
                   fred_api_key=os.environ.get('FRED_API_KEY', ''),
                   sec_rate=float(os.environ.get('PIT_SEC_RATE', '5')),
                   series=tuple(os.environ.get('PIT_ALFRED_SERIES', 'GDP,DGS10').split(',')))

    def validate(self, provider):
        if provider not in {'sec', 'alfred'}:
            raise ProviderError('Unsupported provider')
        if provider == 'sec' and not re.fullmatch(r'[^\r\n]+\s+[^\s@]+@[^\s@]+\.[^\s@]+', self.sec_user_agent.strip()):
            raise ProviderError('SEC_USER_AGENT must identify application and contact email')
        if provider == 'alfred' and not re.fullmatch(r'[a-z0-9]{32}', self.fred_api_key):
            raise ProviderError('FRED_API_KEY must be a configured 32-character lowercase alphanumeric key')


class RateLimiter:
    def __init__(self, monotonic=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = monotonic, sleep
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self, rate):
        with self.lock:
            delay = max(0.0, self.next_at - self.clock())
            if delay:
                self.sleep(delay)
            self.next_at = self.clock() + 1.0 / rate


SEC_LIMITER = RateLimiter()  # Shared by every SEC client/resource in this process.
FRED_LIMITER = RateLimiter()


@dataclass(frozen=True)
class Response:
    provider: str
    resource: str
    body: bytes
    observed_time: str
    status: int
    headers: dict
    attempts: tuple


def request_transport(url, **kwargs):
    # Redirects are deliberately disabled: no credential forwarding or host escape.
    return requests.get(url, allow_redirects=False, **kwargs)


class Client:
    def __init__(self, config, *, transport=request_transport, limiter=None,
                 sleep=time.sleep, clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                 log=None):
        self.config, self.transport = config, transport
        self.limiter = limiter
        self.sleep, self.clock, self.monotonic = sleep, clock, monotonic
        self.log = log or (lambda record: LOG.info('%s', record))

    def fetch(self, provider, path, params=None):
        self.config.validate(provider)
        params = dict(params or {})
        base = 'https://data.sec.gov' if provider == 'sec' else 'https://api.stlouisfed.org/fred'
        if provider == 'sec' and path == '/files/company_tickers.json':
            base = 'https://www.sec.gov'
        # These are internal resource classes, not a caller-controlled HTTP proxy.
        allowed = (r'/files/company_tickers\.json|/submissions/CIK\d{10}(?:-submissions-\d+)?\.json|/api/xbrl/companyconcept/CIK\d{10}/us-gaap/Assets\.json'
                   if provider == 'sec' else r'/series/(?:observations|vintagedates)')
        if not re.fullmatch(allowed, path) or 'api_key' in params:
            raise ValueError('Resource outside pilot boundary')
        resource = base + path + ('?' + urlencode(sorted(params.items())) if params else '')
        headers = {'Accept': 'application/json'}
        if provider == 'sec':
            headers['User-Agent'] = self.config.sec_user_agent
        else:
            params['api_key'] = self.config.fred_api_key
        limiter = self.limiter or (SEC_LIMITER if provider == 'sec' else FRED_LIMITER)
        attempts = []
        for attempt in range(1, self.config.attempts + 1):
            limiter.wait(self.config.sec_rate if provider == 'sec' else 2.0)
            start = self.monotonic()
            response = None
            try:
                try:
                    response = self.transport(base + path, params=params, headers=headers, timeout=self.config.timeout)
                    status = response.status_code
                    retry = 'rate_limit' if status == 429 else 'server' if status in {500, 502, 503, 504} else None
                except (requests.Timeout, requests.ConnectionError):
                    status, retry = None, 'transport'
                except Exception:
                    raise ProviderError('Provider transport failed') from None
                record = {'provider': provider, 'resource_class': path.split('/')[1], 'attempt': attempt,
                          'status': status, 'duration': max(0, self.monotonic() - start), 'retry_class': retry}
                attempts.append(record)
                self.log(record)
                if response is not None and status == 200:
                    # A malicious/changed response echoing the credential is refused before retention.
                    if provider == 'alfred' and self.config.fred_api_key.encode() in response.content:
                        raise ProviderError('Provider response contains credential material; retention refused')
                    safe = {k.lower(): v for k, v in response.headers.items()
                            if k.lower() in {'content-type', 'etag', 'last-modified', 'date', 'retry-after'}}
                    if provider == 'alfred':
                        safe = {k: v.replace(self.config.fred_api_key, '[redacted]') for k, v in safe.items()}
                    return Response(provider, resource, response.content, self.clock().isoformat(), status, safe, tuple(attempts))
                if retry is None or attempt == self.config.attempts:
                    raise ProviderError('Provider request failed: ' + str(status or 'transport')) from None
                delay = min(30, 2 ** (attempt - 1))
                if response is not None:
                    value = response.headers.get('Retry-After')
                    if value:
                        try:
                            requested = float(value)
                        except ValueError:
                            try:
                                requested = (parsedate_to_datetime(value) - self.clock()).total_seconds()
                            except (ValueError, TypeError):
                                requested = 0
                        # Never retry sooner than Retry-After. Long waits defer to the operator/runner.
                        if requested > 60:
                            raise ProviderError('Retry-After exceeds inline retry budget; retry later') from None
                        delay = max(delay, requested)
            finally:
                if response is not None:
                    response.close()
            self.sleep(max(0, delay))


class SECClient(Client):
    def tickers(self):
        return self.fetch('sec', '/files/company_tickers.json')

    def submissions(self, cik, page=None):
        if not re.fullmatch(r'\d{10}', cik):
            raise ValueError('Validated CIK required')
        name = page or 'CIK' + cik + '.json'
        if not re.fullmatch(r'CIK' + cik + r'(?:-submissions-\d+)?\.json', name):
            raise ValueError('Invalid submissions page')
        return self.fetch('sec', '/submissions/' + name)

    def assets(self, cik):
        return self.fetch('sec', '/api/xbrl/companyconcept/CIK' + cik + '/us-gaap/Assets.json')


class ALFREDClient(Client):
    def page(self, series, *, start, end, offset=0, limit=100, observation_start=None,
             observation_end=None, discovery=False, vintage=None):
        from .temporal import day
        if series not in self.config.series:
            raise ValueError('Series outside configured allowlist')
        if day(start) > day(end) or not 1 <= limit <= 1000 or offset < 0:
            raise ValueError('Invalid bounded page')
        params = dict(series_id=series, file_type='json', realtime_start=start, realtime_end=end,
                      offset=offset, limit=limit, sort_order='asc')
        if not discovery:
            if day(observation_start) > day(observation_end):
                raise ValueError('Observation window required')
            params.update(observation_start=observation_start, observation_end=observation_end, output_type=1, units='lin')
            if vintage:
                day(vintage)
                params.pop('realtime_start'); params.pop('realtime_end')
                params['vintage_dates'] = vintage
        return self.fetch('alfred', '/series/' + ('vintagedates' if discovery else 'observations'), params)
