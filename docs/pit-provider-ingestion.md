# Phase 2B: controlled provider collection

Implemented 2026-09-24. This is a small operator-controlled evidence pilot, not a
feature feed. No model training, promotion, binding, forecasting or trading code
consumes these records. Phase 2C has not begun. Live validation is conditional on
real operator configuration; synthetic tests do not establish provider success.

## Provider boundary

`pit/transport.py` contains typed SEC and ALFRED clients using the repository's
existing `requests` dependency. Injected transports, wall/monotonic clocks and
sleep functions make retry/rate tests offline without real sleeps. Responses carry
safe resource identity, bytes, status, selected headers, observed time and attempt
metadata. Transport imports no domain store. Redirects are refused.

`collection.py` retains the entire response through `PITStore.raw` before invoking
`live_parsers.py`. Pure envelope parsing selects rows; `BoundEvidence` feeds those
rows into Phase 2A typed adapters while binding their revisions directly to the
original full response. Extracted JSON is never represented as a provider response.
Raw checksums, parser version and scope are retained. Successful and failed parser
attempts are append-only. Updating a parser requires bumping its envelope version;
old results and normalized history remain. Reparse verifies checksums and performs
no provider request. Equivalent semantic revisions reuse the earliest provenance;
new parser results record the new envelope-to-revision association.

## Official semantics checked 2026-09-24

SEC public data APIs supply submissions and XBRL without an API key. Submissions
include recent filings plus references to older files; the pilot only follows
files whose declared filing dates overlap the requested scope. The concept API
permits a narrow taxonomy/concept request instead of downloading all company facts.
[SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

SEC's ceiling remains 10 requests/second. Automated access needs an identifying
User-Agent; acceptance does not prove public visibility because dissemination lag
is not guaranteed. Current ticker associations do not establish historical ticker
periods or a complete security master.
[SEC developer FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)

FRED requires a 32-character lowercase alphanumeric API key.
[API keys](https://fred.stlouisfed.org/docs/api/api_key.html)
Real-time periods describe inclusive historical knowledge intervals.
[Real-time periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html)
Observation requests default to today's real-time dates, so this collector always
specifies start/end. Output type 1 preserves intervals and untransformed values;
the typed client also supports an explicit single vintage date. The documented
observation limit is 100,000; the pilot uses 100 by default and allows at most 1,000.
[Observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
Vintage discovery returns release/revision dates and count/offset/limit metadata;
its documented limit is 10,000, also restricted to the smaller pilot page size.
[Vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)

## Configuration and requests

Set real configuration in the process environment. This repository has no existing
`.env.example` convention, so no secret-loading dependency or example env file was
introduced.

- `SEC_USER_AGENT`: application name followed by the operator's real contact email.
  Missing/invalid configuration fails before requests. No automatic identity is supplied.
- `PIT_SEC_RATE`: default 5 requests/second; positive values at most 10 only.
  A process-wide locked limiter covers every SEC client/resource, including retries.
- `FRED_API_KEY`: required for ALFRED; never included in client repr, safe resource
  identities, exceptions, metadata, operational inputs or logs. Redirects are disabled.
- `PIT_ALFRED_SERIES`: default `GDP,DGS10`; at most three explicit series. GDP is
  selected for revision testing; DGS10 for daily observations and missing holiday
  values. These are pilot choices, not existing model features or a guarantee that
  a particular date window contains every desired example.
- `PIT_ENABLE_REFRESH=1`: explicitly registers SEC/ALFRED refresh jobs with the
  existing Phase 0 scheduler. Absent by default. No automatic backfill registration.

Default timeout is 30 seconds and maximum attempts is three. Retry only 429,
500/502/503/504, timeout and connection errors. Backoff is 1 then 2 seconds; there
is no existing jitter abstraction to reuse. Numeric and HTTP-date Retry-After
values take precedence. Values beyond 60 seconds stop inline retries and require
a later attempt; the collector never retries early. Other statuses, schema and
parser failures are not blindly retried. FRED uses a conservative 2 requests/second
process-wide limiter. Multiple independent processes do not coordinate HTTP rates;
operator CLI/Phase 0 uses the shared worker lock, and collection writes use a second
cross-process lock. Do not run unrelated SEC workloads to evade aggregate limits.

HTTP logs and durable attempt rows include provider, resource class, attempt,
status, duration, retry class and collection identity. A retention log associates
that identity with the raw ID. No full credential-bearing URL is logged. Allowlisted
response headers are retained; credential echoes in headers are redacted. A response
body containing the configured FRED key is refused without retention or body logging
and the collection fails visibly. This exceptional credential-protection boundary
is distinct from ordinary parser failures, whose bytes always survive.

## SEC scope and identity

AAPL is already in the repository's research universe. It is the sole pilot ticker;
its CIK is **not** hard-coded in production. The collector first retains and parses
`www.sec.gov/files/company_tickers.json`. A single supported current AAPL/CIK issuer
association is required. Conflicting entries or existing ambiguous CIK aliases block
normalization and surface a reconciliation finding. No nearest-name matching occurs.

The issuer has a separate internal entity ID and sourced CIK alias with raw evidence.
This evidence does not prove a distinct share class, security or historical listing:
no security/listing or ticker history is fabricated. Coverage explicitly reports
unresolved security mappings. More class-level evidence is needed before mapping a
security in a future scoped task. Entity-level filings and facts remain queryable.

Resources after discovery:

1. `data.sec.gov/submissions/CIK<resolved>.json`.
2. Only overlapping referenced `CIK<resolved>-submissions-*.json` pages.
3. `data.sec.gov/api/xbrl/companyconcept/CIK<resolved>/us-gaap/Assets.json`.

The pilot collects selected filing metadata, not filing-document bodies or a full
archive. Default forms are 10-K, 10-Q and their amendments. 8-K/8-K/A can be explicitly
selected through the typed service. CIK/accession/form/filing date/report period/
acceptance/document name survive normalization. Acceptance stays source time and an
explicit `sec_acceptance_proxy_v1`; strict historical queries exclude it. `/A` filings
remain distinct, with unresolved lineage unless separately proven under Phase 2A.

XBRL supports USD Assets instant facts within the filing window, including taxonomy,
concept, unit, period, empty dimensions, accession and value. Unsupported dimensional,
duration or non-USD facts remain in raw evidence and are counted as unsupported in
parser history. No coercion occurs. Filed date alone remains unknown availability.

## ALFRED historical behavior

Each allowlisted series has bounded vintage discovery followed by bounded observation
requests using explicit real-time and observation windows. This preserves every returned
revision interval rather than accidentally taking today's snapshot. A requested period
may clip provider intervals; the collector never extends them beyond returned evidence.
After that interval, an as-of query can legitimately return no value. There is no
latest-value fallback. A date-level policy uses `America/Chicago` and next local
midnight, explicitly not a proven intraday publication time. Missing `.` stays missing;
no fill or transformation is applied. Revision selection is the Phase 2A query policy.

## Bounds, checkpointing and idempotency

Every scope requires start/end, and ALFRED additionally requires an observation window.
Both windows are limited to 366 days. Requests ending after the local observation date
are rejected. The total page budget defaults to 12 and cannot exceed 30; budget exhaustion
is incomplete/blocked, never marked complete. Split a larger backfill into explicit
small scopes; there is no download-everything override in this pilot.

Collection identity hashes provider scope and a supplied execution key. Reusing the key
resumes the same logical work; a new key explicitly requests another provider observation.
Page identity is SQL-unique within a collection. Before parsing, a checkpoint pins the
retained raw ID and actual HTTP observation time. Restart verifies checksums and reuses
pinned pages without HTTP. Interrupted pages can be reparsed safely. Count drift,
incomplete arrays, unexpected offsets and page-budget exhaustion remain visible failures.
SEC recent/history feeds share logical accession identity, including overlap between files.

Raw reuse uses source + safe resource + bytes, so identical recollection reuses raw and
logical records while a new collection/page observation records the successful check time.
Changed bytes remain additional immutable evidence. Logical events have Phase 2A SQL
uniqueness, and equivalent revisions are reused under the cross-process collection lock.
The raw artifact and SQLite cannot commit atomically; existing artifact audit exposes
orphan bytes after a crash, and never deletes them automatically.

States are planned (zero-network plan), running, completed, failed or blocked. Dry-run
prints scope, page budget and rates without opening a database or contacting providers.
The planned state is a plan result; durable collection rows begin running on execution.

## Phase 0 jobs and failure recovery

Operator commands use the existing JobRunner/JobStore, shared worker lock and effect
fence, without constructing a training runtime. Default scheduling is unchanged. Optional
`pit-refresh:sec` and `pit-refresh:alfred` jobs use the existing daily schedule and a pinned
30-day window derived from the scheduled slot. Refresh is distinct from historical backfill.

HTTP retries stay within the same operational execution. Any post-fence failure remains
blocked under Phase 0 rules. An explicit `--resume` starts an operator continuation using
the same collection key and proven checkpoints; the old blocked operational history remains
unchanged and may need normal manual reconciliation. No automatic ambiguous repair occurs.
Do not confuse a new execution key (a fresh check) with resuming an old one.

## Commands and read-only API

All output is JSON. Use the existing environment's Python and put database before the command.
Before applying Phase 2B to an existing database, run the backup-first migration:

```bash
python -m stock_app.research data --database artifacts/research/research.sqlite3 migrate-providers
python -m stock_app.research data backfill sec --start 2025-01-01 --end 2025-03-31 --dry-run
python -m stock_app.research data collect sec --start 2025-01-01 --end 2025-03-31 --execution-key sec-pilot-1
python -m stock_app.research data backfill alfred --series GDP,DGS10 --start 2025-01-01 --end 2025-03-31 --observation-start 2025-01-01 --observation-end 2025-03-31 --execution-key alfred-pilot-1
python -m stock_app.research data provider-status
python -m stock_app.research data coverage
python -m stock_app.research data raw show RAW_ID
python -m stock_app.research data raw verify RAW_ID
python -m stock_app.research data raw reparse RAW_ID
python -m stock_app.research data audit --depth artifact
python -m stock_app.research data as-of --time 2025-02-15T12:00:00Z --strictness allow_proxy
python -m stock_app.research data leakage-check manifest.json
```

Repeat a successful scope with a new execution key to test actual recollection. Repeat
with the same key to test logical job idempotency. Resume an interrupted scope with the
same key and `--resume`. Failed operator jobs exit nonzero.

GET-only `/api/research/pit/provider-status`, `/coverage`, and `/raw/<id>` join existing
PIT query/history/audit endpoints. No collection/backfill/reparse HTTP mutation endpoint
exists. Raw show/API exposes metadata; bytes remain local artifacts referenced by path.

Freshness is the most recent successful stored HTTP observation per provider, even when
nothing new was released. Coverage reports stored filing/realtime ranges separately.
Collector success is not a claim that a new economic release occurred. Counters report
requests, success, retries, 429s, parse failures, stored raw/events, created/reused revisions
and current collection findings. Parser failures and partial collections enter the existing
PIT audit → Phase 1.5 reconciliation path; detection does not alter identity history.

## Database and preservation

Four additive tables: `pit_collections`, `pit_collection_pages`, `pit_parse_results`,
`pit_http_attempts`. Page uniqueness and append-only triggers protect evidence/history;
only collection status/progress is mutable. New indexes cover raw source/checksum/resource,
parser-result lookup and event type. Existing revision and identifier indexes now support
actual keyed queries: primary-ID reads, CIK resolution and event-specific revision retrieval
avoid whole-table reads. An unfiltered audit/query still inspects its requested universe.

Migration takes `worker.lock`, backs up SQLite, hashes every pre-existing table including
Phase 2A, and hashes registered model files. It compares those inventories after additive
DDL. It neither seeds live records nor rewrites provenance. Keep the backup and report.

This remains SQLite pilot storage. Before exceeding 30 pages per scope or 100,000 stored
revisions, benchmark realistic filtered as-of and coverage workloads and inspect EXPLAIN
QUERY PLAN again. Slow filtered queries, write-lock contention or unmanageable artifact
volume justify a separate storage proposal; those thresholds are review triggers, not
measured capacity guarantees. Coverage and artifact audit still scan the pilot universe.

## Testing and known limitations

`tests/test_pit_collection.py` uses deliberately synthetic live-shaped responses, fake
clocks/transports and no real sleeps. It does not claim those fixtures were downloaded.
The normal suite does not contact either provider. Opt-in smoke tests use the real
collector boundary and report their temporary evidence directory:

```bash
PIT_LIVE_SMOKE=1 python -m pytest tests/test_pit_live_smoke.py -q -s
```

Missing credentials skip the relevant live test. Use operator CLI when evidence needs to
be retained in the working research directory. A live smoke test validates raw checksums,
normalization, recollection, reparse and a historical query; actual amendment availability
or a revised/missing observation still depends on the selected real window. Inspect results
before claiming every live example. Provider historical corrections after a pinned page
are deliberately not merged into a resumed collection; use a fresh execution key.

Current delivery has no configured live credentials, so live SEC identity, actual amendment
examples and ALFRED revised/missing values have not been verified. Unsupported XBRL remains
raw-only. Security-class mapping remains unresolved. These limits must remain visible in
any Phase 2C proposal. Phase 2C should separately design an explicit frozen-manifest feature
consumer with temporal validation; no automatic feature inclusion or model activation.
