# Phase 2A completion report

Verified locally 2026-09-23. No Phase 2B ingestion/backfill, model retraining,
active-model change, LLM agent or trading was introduced. Earlier uncommitted work
was preserved. See [the service/operator contract](point-in-time-data.md).

1. **Existing architecture.** Yahoo raw CSV → validation/corporate-action adjustment
   → immutable adjusted snapshot → causal features → registered experiment. Phase 0
   supplies job/lock/effect fencing; Phase 1/1.5 supply frozen inputs and evidence review.
2. **Current-vintage risks.** Repeatable downloaded history is not historical
   knowledge: revisions, current adjustments, restated fundamentals and present-day
   symbol membership can leak later information. Existing data is not relabeled PIT.
3. **Sources.** Immutable stable provider/feed IDs with schema, type and temporal
   capabilities. Downstream FK references replace free-form source strings in the
   new architecture. Multiple SEC feeds remain distinct.
4. **Identity model.** Permanent typed entity → security → listing hierarchy,
   explicit internal keys/evidence, exchange listings and sourced external aliases.
   Entity-as-issuer can own multiple securities. CIK is SQL-restricted to entities.
5. **Legacy symbols.** Unmapped AAPL returns `legacy_symbol`, no permanent ID.
   Existing Yahoo/prediction consumers remain unchanged. Dated alias resolution
   rejects unknown/ambiguous periods rather than inventing history. An ongoing interval
   requires explicit open-ended evidence; a null end alone does not imply infinity.
6. **Raw evidence.** Immutable bytes precede parsing; checksum-addressed storage
   deduplicates identical payloads. Separate observations retain resource/source,
   parser version, observed/ingested times and optional metadata. Changed responses
   and failed-parser inputs survive.
7. **Events/revisions.** Logical provider/type/key identity is separate from immutable
   revision content/provenance. Composite FKs enforce source ownership; supersession
   stays within the event. Explicit amendment relations preserve original filings.
8. **Temporal fields.** Event time/range, source time, availability, observation and
   ingestion remain distinct. Date/instant precision is retained; exact instants
   require timezones; no period-end-to-publication conversion occurs.
9. **Availability.** `exact`, `date_level`, `proxy`, `observed_only`, `unknown`, each
   with basis. Proxy policy cannot be labeled exact. Computed date eligibility bounds
   are distinct from source timestamps.
10. **Query policies.** Mandatory cutoff and explicit `strict`, `allow_proxy` or
    `observed_only`. Strict accepts exact historical evidence only. Date/proxy mode
    retains labels; date-only facts use explicit timezone/end-of-day policy. Local
    observation is a separate guarantee.
11. **SEC contract.** Offline minimal filing adapter preserves CIK/accession/form,
    report period, filing date, acceptance datetime and primary document. Acceptance
    is a labeled proxy, never guaranteed exact public availability. Official API/FAQ
    semantics, User-Agent, 10 requests/sec limit and mapping caveats were checked.
12. **Amendments.** `/A` filings do not replace originals. Linking requires explicit
    original ID/evidence and matching entity/form family. Unproven lineage remains
    unresolved and appears in audit/reconciliation findings.
13. **XBRL.** Entity, taxonomy, concept, unit, period and dimensions define logical
    identity; accession, numeric value and raw evidence define revision provenance.
    Filed date alone leaves historical availability unknown. No massive normalizer.
14. **ALFRED.** Series/observation identity preserves value, realtime start/end and
    requested vintage separately. Inclusive realtime periods and revised values
    survive normalization; missing values remain missing. No fabricated intraday time.
15. **As-of behavior.** Historical cutoff selects eligible older revisions, not
    today's latest. Conflicting ties remain ambiguous. Identical repeated evidence
    can use its earliest observation; re-downloading an old vintage cannot replace
    a newer source vintage. Expired ALFRED values never silently fill a missing vintage.
16. **Leakage detector.** Safe/warning/invalid/unverifiable findings compare frozen
    manifests to revision/raw identity, fingerprints, cutoff and policy. Detects
    future-known inputs, proxy/observed evidence masquerading as strict proof,
    missing temporal metadata and wrong historical revision selection. No repair.
17. **Registry integration.** New specs may contain `data.pit_manifest`; new runs
    must carry identical provenance. Old specs/runs remain byte-for-byte unchanged.
    Existing model engines do not consume PIT inputs or silently accept new contracts.
18. **Integrity/reconciliation.** Phase 1.5 metadata review follows revision → raw
    evidence → fingerprint → cutoff; artifact mode verifies raw bytes. PIT audit
    findings materialize through existing `integrity reconcile detect`. No new
    reconciliation framework or automatic identity resolution.
19. **Audit.** Read-only counts, fingerprints/IDs/FKs, missing/corrupt/orphan raw
    evidence, temporal/lineage conflicts, uncertain/overlapping aliases, availability
    categories and optional future-known counts. Default audit uses no provider or
    artifact byte reads.
20. **CLI/API.** JSON `data sources|resolve|security|identifiers|history|as-of|audit|
    leakage-check`. Six GET-only endpoint families under `/api/research/pit`.
    Internal typed ingestion only. The optional Phase 0 handler reuses its effect
    fence/blocked semantics and registers no jobs or schedules.
21. **Schema.** Six additive tables: `pit_sources`, `pit_identities`,
    `pit_identifiers`, `pit_raw`, `pit_events`, `pit_revisions`. Foreign keys,
    uniqueness, immutable triggers, parent/source/lineage/temporal checks and indexes.
22. **Migration.** Worker lock, backup first, all-original-table inventory, model
    hashes, additive installation and before/after verification. Restart safety is
    covered by tests. No historical record was converted or source identity seeded.

    Backup: `artifacts/research/research.before-pit-20260923T065536-5e8b3d2d.sqlite3`.
    Inventory: `artifacts/research/pit-migration-verification-20260923T065536-5e8b3d2d.json`.
    Final audit/preservation evidence: `artifacts/research/pit-verification.json`.

23. **Preservation results.** Every original table row hash and registered model
    file hash matches before/after and was checked again after implementation.

    | Original data | Before | After |
    | --- | ---: | ---: |
    | Records | 1,328 | 1,328 |
    | Active bindings / model files | 7 / 17 | 7 / 17 |
    | Canonical issuances | 22 | 22 |
    | Forecast revisions / migration links | 797 / 797 | 797 / 797 |
    | Registry runs/specs/attempts/outcomes | 10 each | 10 each |
    | Registry artifacts / model links | 20 / 10 | 20 / 10 |
    | Integrity reproduction starts / results | 10 / 10 | 10 / 10 |
    | Integrity cases / actions | 10 / 0 | 10 / 0 |

    Registry audit remains `ok`; forecast ledger remains `healthy`. Research
    integrity retains only its ten legacy gaps. PIT audit is `ok` with zero live
    sources/identities/events/revisions: this validates installation, not provider
    coverage. Synthetic fixture records exist only in isolated tests.
24. **Files changed for this phase.** Added `stock_app/research/pit/` modules:
    `__init__.py`, `schema.py`, `temporal.py`, `store.py`, `query.py`, `providers.py`,
    `audit.py`, `integration.py`, `migration.py`, `cli.py`; added
    `tests/test_point_in_time_data.py` and five synthetic JSON fixtures plus provenance
    README under `tests/fixtures/pit/`; added the two point-in-time documents.
    Extended `stock_app/research/{store,__main__,api,registry,integrity,
    integrity_reconcile}.py`. Updated platform/registry documentation and only the
    roadmap status paragraph. No earlier tests or model math files were edited.
25. **Tests added.** 101 offline tests cover source/identity/alias invariants,
    permanent versus provider identities, raw deduplication and failed parsing,
    revision immutability/lineage/concurrency, five-time semantics and timezone/DST
    boundaries, strict/proxy/observation behavior, vintage selection and expired
    fallback rejection, SEC filings/amendments/XBRL, ALFRED vintages, leakage and
    PIT spec/run provenance, offline audits, CLI/GET-only APIs, operational fencing,
    reconciliation detection, migration preservation and SQL constraints.
26. **Final verification.** **684 passed in 44.11 seconds**: all 583 baseline tests
    plus 101 new tests. The full suite includes operational, canonical forecast,
    registry, integrity, research/evaluation and PIT tests. Python compilation,
    tracked/new-file whitespace checks, and all three JavaScript regression scripts
    passed. No provider was contacted by tests; official documentation browsing was
    separate from tests and ingestion.
27. **Risks/limits.** No globally complete security master or live parser coverage.
    The offline adapters support minimal representative records, not complete SEC
    or ALFRED response normalization. Date-level policy is conservative and explicit,
    not exact-publication proof. Local storage is owner-controlled, not tamper-proof.
    Source assertions remain assertions requiring evidence review. Current table
    scans need query-plan/pagination work before large backfills. Conflicting mapping
    corrections require future explicit versioning/review; no automatic cleanup or
    identity rewrite. Phase 1.5 model serialization differences and legacy evidence
    gaps remain unchanged.
28. **Recommended Phase 2B.** Separately authorize a small controlled SEC/ALFRED
    collector pilot, declared User-Agent/rate limits, secure FRED credentials,
    real response contract tests, pagination/idempotence, bounded backfill,
    evidence-backed identity resolution, vintage QA and operational freshness/
    reconciliation gates. Keep training/promotion/trading separately scoped.
    **Phase 2B has not started.**
