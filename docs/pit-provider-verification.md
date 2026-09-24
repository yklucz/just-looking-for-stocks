# Phase 2B verification and handoff — 2026-09-24

Phase 2B implementation is complete and verified offline. Live SEC/ALFRED validation
was **not executed**: neither `SEC_USER_AGENT` nor `FRED_API_KEY` is present. No live
PIT records or fabricated identities were seeded. Phase 2C has not begun.

## Required delivery report

| # | Area | Result |
| --- | --- | --- |
| 1 | Provider clients | Typed SECClient/ALFREDClient use the existing requests dependency; transport, retained evidence, pure envelope parsing and typed normalization are separate. |
| 2 | SEC official semantics | Rechecked 2026-09-24: 10 requests/second ceiling, identifying User-Agent, public data resources, historical submissions and non-guaranteed dissemination lag. Official links are in the ingestion guide. |
| 3 | SEC configuration | Environment-provided application/contact User-Agent; missing or invalid values fail before HTTP. No default fake identity. |
| 4 | SEC rate/retry | Process-wide default 5 requests/second; at most 10. Three bounded attempts for 429, selected 5xx, timeout/connection errors; numeric/date Retry-After honored or deferred when above inline budget. |
| 5 | SEC resources | Implemented ticker/CIK evidence, submissions/referenced overlapping history, narrow us-gaap/Assets concept endpoint. No live resources collected; offline live-shaped fixtures only. Filing-document bodies and general archive crawling are outside the pilot. |
| 6 | Identity | AAPL-only discovery from retained SEC evidence; separate internal entity and CIK alias. No hard-coded production CIK. Ambiguity blocks parsing. Security-class mapping remains unresolved; no security/listing or ticker history is fabricated. |
| 7 | Filings | Accession identity, form, filing date, report period, acceptance, document name and raw provenance preserved. Acceptance is a labeled proxy; strict queries exclude it. |
| 8 | Amendments | Distinct accession events preserved; original lineage unresolved without explicit evidence. Offline `/A` behavior verified, real examples not verified. |
| 9 | XBRL | USD Assets instant facts retain concept, unit, period, accession, value and raw provenance. Unsupported shapes remain raw and are counted; filed date never becomes exact availability. |
| 10 | ALFRED official semantics | Rechecked 2026-09-24: key requirements, inclusive realtime intervals, today-default observation behavior, explicit vintages, count/offset pagination and documented limits. |
| 11 | Credentials | Key stays in the transport request only; safe resource IDs, metadata, logs, repr and errors exclude it. Secret-echo responses are refused visibly before retention. Tests scan stored metadata/history. |
| 12 | Pilot series | Configured small allowlist defaults to GDP/DGS10 for revision and daily/missing-value coverage. No model feature integration. |
| 13 | Vintages/revisions | Explicit bounded realtime queries retain separate revisions and `.` missing values. Offline historical queries select 100 before the revision and 103 afterward. Returned interval ends are honored; no extrapolation/latest fallback. |
| 14 | Raw retention | Complete response bytes stored before parser invocation; checksum, safe resource, source, selected HTTP metadata, observation and parser version retained. Parse failures preserve bytes. |
| 15 | Reparse | Checksum-verified, zero-network offline reparse; immutable parser-result history; equivalent facts reuse revisions while new results link additional retained envelopes. |
| 16 | Pagination | SEC follows only overlapping provider-referenced pages. ALFRED checks count, offset, size and completion; changed totals or incomplete pages block completion. |
| 17 | Backfill boundaries | Required windows at most 366 days; ALFRED also requires observation bounds. Default 12 pages, maximum 30; no unbounded override. Zero-network JSON plan. |
| 18 | Resume | SQL-unique page checkpoints pin raw IDs before parsing. Restart verifies bytes, reparses and skips completed HTTP pages. Explicit operator continuation preserves prior blocked job history. |
| 19 | Idempotency | Stable scope/execution identity; raw source/resource/content dedup; shared filing accession identity across recent/history; equivalent revision reuse under process/file locks; SQL uniqueness and concurrent tests. |
| 20 | Freshness | Successful HTTP observation time, including unchanged responses, is distinct from stored filing dates/vintage ranges. A completed past job is not a fresh provider check. |
| 21 | Reconciliation | Incomplete collections and parser/checksum/identity failures feed PIT audit and existing Phase 1.5 cases. No automatic ambiguous mapping or history repair. |
| 22 | Coverage | Actual stored issuer/security counts, unresolved mapping count, filings/range, XBRL facts, macro series/observations/revisions/vintage range, raw count and latest completed collection. No full-archive claim. |
| 23 | Audit/leakage | Working registry `ok`, forecast ledger `healthy`, PIT artifact audit `ok`; exactly 10 existing integrity findings. Offline fixture audit and proxy leakage/strict exclusion tests pass. No live leakage claim. |
| 24 | CLI/API | JSON collect/backfill/plan/resume, provider-status, coverage, raw show/verify/reparse and migration. Existing audit/as-of/leakage commands retained. Three new GET-only API routes; no public provider mutations. |
| 25 | Database/indexes | Four additive collection/history tables. Indexed raw lookup, parser lookup, event types; keyed PIT reads and CIK lookup; indexed event revision retrieval. EXPLAIN checks pass. SQLite retained. |
| 26 | Live result | Not run: SEC_USER_AGENT and FRED_API_KEY both absent. Two explicit opt-in smoke tests skip in the default suite. No live evidence was invented or downloaded through another route. |
| 27 | Migration | Shared worker lock acquired; SQLite backup created before DDL. Idempotent migration tested. All 27 pre-existing tables inventoried, including all six Phase 2A tables. |
| 28 | Preservation hashes | All 27 table count/content hashes match. All 17 model records match; 10 existing model files hash identically and seven pre-existing missing artifact states remain unchanged. Seven active bindings, 22 forecast issuances, 797 revisions, 10 research runs/specs, and all integrity history preserved. |
| 29 | Changed files | Exact list below: client/collector/parser/schema/migration/job modules, small integration changes, docs and new tests. No dependencies, feature code, forecast mathematics or model bytes changed. |
| 30 | New tests | 107 offline test cases plus two opt-in live smoke cases. Fake clocks/transports, no timing sleeps; unmocked provider HTTP is forbidden in the offline collection test module. |
| 31 | Full suite | **791 passed, 2 skipped**. All 684 prior tests remain passing, unweakened. Compilation, whitespace check and all three JavaScript regression scripts pass. |
| 32 | Remaining risks | Real provider/schema/access behavior unverified here; security class unresolved; no real amendment/revision/missing example confirmed; unsupported XBRL raw-only; paginated provider changes block rather than merge; artifacts and SQLite are not one atomic transaction; unfiltered coverage/audit scan the pilot universe. |
| 33 | Phase 2C recommendation | Separately authorize a frozen-manifest feature consumer only after live evidence and temporal quality review. Define date/proxy policy and feature qualification explicitly. No automatic training, promotion or binding changes; do not start it as part of this delivery. |

## Commands verified

- `.venv/bin/python -m compileall -q stock_app tests`
- `git diff --check`
- `node scripts/test_chart_data.mjs`
- `node scripts/test_frontend_contract.mjs`
- `node scripts/test_research_frontend.mjs`
- `.venv/bin/python -m pytest tests/test_pit_collection.py tests/test_point_in_time_data.py tests/test_operational_jobs.py -q`: 243 passed.
- `.venv/bin/python -m pytest -q`: 791 passed, two explicit live tests skipped. Includes operational, canonical forecast, registry, integrity, PIT 2A/2B and research/evaluation tests.

## Local preservation evidence

These ignored operational artifacts are local, not committed source:

- `artifacts/research/research.before-provider-20260924T012500-669ee464.sqlite3`
- `artifacts/research/provider-migration-20260924T012500-669ee464.json`
- `artifacts/research/provider-verification.json`

The migration report stores the full before/after hashes. Production PIT source,
identity, identifier, raw, event and revision counts all remain zero. New collection
tables contain no production runs. Read-only verification does not materialize
new reconciliation cases or rewrite existing history.

## Files changed

New:

- `stock_app/research/pit/transport.py`
- `stock_app/research/pit/live_parsers.py`
- `stock_app/research/pit/collection.py`
- `stock_app/research/pit/collection_schema.py`
- `stock_app/research/pit/collection_jobs.py`
- `stock_app/research/pit/collection_migration.py`
- `tests/test_pit_collection.py`
- `tests/test_pit_live_smoke.py`
- `docs/pit-provider-ingestion.md`
- `docs/pit-provider-verification.md`

Updated:

- `stock_app/research/store.py`
- `stock_app/research/pit/store.py`
- `stock_app/research/pit/providers.py`
- `stock_app/research/pit/query.py`
- `stock_app/research/pit/audit.py`
- `stock_app/research/pit/cli.py`
- `stock_app/research/api.py`
- `stock_app/jobs/service.py`
- `docs/point-in-time-data.md`
- `docs/agentic-stock-research-roadmap.md` (status only)

[Operator guide and official references](pit-provider-ingestion.md)
