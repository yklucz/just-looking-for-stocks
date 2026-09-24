# Phase 2B.1 live validation gate — 2026-09-24

**Gate not passed: required configuration is absent.** No SEC or ALFRED data
requests were sent. No real-provider evidence was obtained through another route.
Phase 2C is not cleared by this gate and has not started. Existing Phase 2B changes
were preserved; no collector, parser, test, model or roadmap changes were made.

## Official documentation rechecked

Verified on 2026-09-24, independently of the unavailable data-request credentials:

- SEC requires an identifying application/contact User-Agent and limits automated
  access to 10 requests/second. Acceptance-to-public-visibility lag is not guaranteed;
  acceptance must remain a proxy. The configured pilot rate remains 5 requests/second.
  [SEC FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions)
- Public submissions metadata references older filing files; the company-concept
  endpoint returns facts grouped by unit for one issuer/taxonomy/concept. No broader
  archive retrieval is needed for the planned AAPL/Assets path.
  [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- FRED requires a configured API key.
  [API-key documentation](https://fred.stlouisfed.org/docs/api/api_key.html)
- FRED documents up to 120 requests/minute and 429 throttling, with possible temporary
  blocking if throttling is ignored. The existing 2 requests/second setting equals
  that nominal per-minute ceiling; it should not be described as below-ceiling.
  No rate was increased or changed, and no FRED requests were attempted.
  [FRED errors/rate limits](https://fred.stlouisfed.org/docs/api/fred/errors.html)
- Realtime periods are inclusive historical intervals. Observation realtime parameters
  default to today, so historical collection must remain explicit; vintage dates are
  an alternative historical selection mechanism. Observation pagination uses count,
  offset and limit, with a documented maximum limit of 100,000.
  [Realtime periods](https://fred.stlouisfed.org/docs/api/fred/realtime_period.html),
  [observations and vintages](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- Vintage discovery exposes count/offset/limit with a documented maximum limit of
  10,000. The existing smaller pilot bounds remain unchanged.
  [Vintage discovery](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)

Documentation review is not verification of real response shapes or access behavior.

## Required results

| # | Check | Outcome |
| --- | --- | --- |
| 1 | Configuration | SEC_USER_AGENT absent; FRED_API_KEY absent. Configured allowlist GDP,DGS10; SEC rate 5/second. No values invented. |
| 2 | Semantics verification | Official pages rechecked 2026-09-24; references above. |
| 3 | SEC requests | Zero data requests; corresponding pilot not executed. |
| 4 | SEC raw evidence | None retained; production raw count remains zero. |
| 5 | SEC identity | Not verified live; no CIK, issuer, security or listing created. |
| 6 | Filings | No real filings normalized. |
| 7 | Amendments | Not observed because pilot was not executed; not a claim that a bounded real sample lacked amendments. |
| 8 | XBRL | No real Assets fact collected or normalized. |
| 9 | SEC idempotency | Not tested live. All production PIT counts remain 0 → 0 from no collection, not from demonstrated recollection. |
| 10 | ALFRED requests | Zero data requests; corresponding pilot not executed. |
| 11 | GDP vintages | No real observations/revisions queried; live causal behavior unverified. |
| 12 | DGS10 | No real daily or missing observations collected. |
| 13 | ALFRED idempotency | Not tested live; unchanged zero counts do not prove live idempotency. |
| 14 | Credential checks | No live key exists to scan for. Synthetic key-isolation tests pass in the offline suite; this does not establish live-key leak validation. Generated reports contain configuration presence only. |
| 15 | Offline reparse | No retained live payload exists for either provider. Existing synthetic reparse tests pass; the live-evidence requirement remains unmet. |
| 16 | Contract differences | No response-shape differences discoverable without live requests. Documentation review clarified FRED's 120/minute ceiling. |
| 17 | Parser/test changes | None. No demonstrated live contract issue justifies a parser change. |
| 18 | PIT audit | Artifact audit `ok`, with no production PIT evidence. Registry `ok`; forecast ledger `healthy`. |
| 19 | Leakage | Live checks not executed. Offline strict SEC proxy rejection, allow-proxy selection and historical ALFRED behavior remain fixture-only. Empty production queries are not presented as evidence of a successful live leakage test. |
| 20 | Coverage | Zero sources, identities, aliases, raw records, events and revisions; zero collections/pages/parser results/HTTP attempts. |
| 21 | Unresolved findings | Missing configuration blocks the gate. No new mapping was attempted. The same 10 legacy research-integrity findings remain; no new cases/actions materialized. |
| 22 | Preservation | Worker lock acquired; fresh SQLite backup and machine-readable pre/post inventories. All 31 current table count/content hashes match, including the 27 prior tables and four collection tables. Seventeen model records unchanged: ten existing files hash identically, seven missing-artifact states unchanged. Seven active bindings, 22 issuances, 797 forecast revisions, registry and integrity history preserved. |
| 23 | Offline suite | 791 passed, two default live-test skips. Python compilation, whitespace and all three JavaScript regression scripts pass. Full suite includes all requested test groups. |
| 24 | Explicit smoke | Invoked separately with PIT_LIVE_SMOKE=1: two tests skipped specifically for absent/invalid provider configuration. Zero live tests passed; no live failure hidden. |
| 25 | Files changed | This report plus ignored local backup/inventory/verification/test-log artifacts. All pre-existing uncommitted Phase 2B changes retained. Roadmap unchanged. |
| 26 | Acceptance | **Not passed.** Live-request, real-evidence, normalization, idempotency, historical selection, reparse and successful live-smoke requirements remain unmet. Preservation and offline regression checks passed. |
| 27 | Phase 2C | Not cleared by this gate. Supply valid provider configuration through the existing environment and rerun bounded validation before making a Phase 2C readiness decision. No Phase 2C implementation performed. |

## Evidence artifacts

Ignored local directory: `artifacts/research/live-provider-validation-20260924/`.

- `research.before-live.sqlite3`: backup taken while holding the shared worker lock.
- `pre-pilot.json`: timestamp, backup path and all table/model inventories.
- `verification.json`: post inventory, audits, coverage, outcomes and acceptance status.
- `offline-tests.log`: full suite output.
- `live-smoke.log`: explicit credential-gated smoke results and skip reasons.

Commands: `.venv/bin/python -m pytest -q`;
`PIT_LIVE_SMOKE=1 .venv/bin/python -m pytest tests/test_pit_live_smoke.py -q -rs`;
`.venv/bin/python -m compileall -q stock_app tests`; `git diff --check`;
`node scripts/test_chart_data.mjs`; `node scripts/test_frontend_contract.mjs`;
`node scripts/test_research_frontend.mjs`.

No working model was trained, candidate generated, forecast issued, binding changed,
feature integrated or integrity history rewritten during this validation gate.
