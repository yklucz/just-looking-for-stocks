# Phase 1.5 completion and verification report

Verified locally on 2026-09-23. Scope ends at Phase 1.5; Phase 2 and the AI agent
remain unimplemented. The pre-existing uncommitted Phase 0/0.2/1 work was preserved.

1. **Existing capabilities discovered.** Registry audit already checked content
   hashes, foreign keys, outcomes, orphan files and model links. Candidate execution
   froze input/report bytes and code/dependencies. Registry replay created a new
   research execution; forecast replay already verified a retained revision without
   issuing a forecast. Phase 0 already had locks, effect fencing and operator review.
2. **Evidence architecture.** New read-only service follows question/hypothesis,
   family/spec/trials, run/attempt/outcome, input, checkpoint/report/candidate and
   canonical forecast edges. No runtime initialization or provider request is needed.
3. **Manifest contract.** Deterministic canonical JSON/SHA-256 references immutable
   records and artifacts, contracts, statistical-report references, dataset metadata,
   original execution times and forecast authority. Volatile inspection, heartbeat,
   lifecycle and forecast-resolution state are excluded.
4. **Verification levels.** Metadata checks relationships, stored identity and
   chronology; artifact adds byte/readability/frame checks; explicit reproduction
   adds offline execution/comparison. Default audit never executes replay.
5. **Evidence statuses.** `verified`, `missing`, `mismatch`, `unverifiable`,
   `not_applicable`. Overall `verified`, `incomplete`, or `invalid`; unexamined bytes
   and absent historical preregistration are not presented as verified.
6. **Reproduction architecture.** Separate verification start/result tables and
   request output directories. Existing candidate engine/math is reused with
   registration hooks suppressed. No new ExperimentRun, candidate registration,
   model activation, forecast or discovery is created.
7. **Comparison policies.** Versioned `float64-v1`: exact identities, counts,
   configuration, boundaries, categorical values and artifact hashes; other finite
   floats use relative `1e-10` and absolute `1e-12`. No caller-supplied tolerance.
   Nested report comparison includes baselines, observations/predictions and bootstrap
   evidence. Differences remain explicit and retain recorded/replayed values.
8. **Reproduction record design.** Unique request key, original run, immutable
   start, manifest/code/environment fingerprints, fixed policy, at most one terminal
   result, diagnostic classification and timestamped fingerprinted certificate.
   Crashed starts remain inspectable; retries of the same key cannot refit silently.
9. **Reconciliation case design.** Typed source/category, deterministic detection
   identity, related IDs, severity, evidence and detected time. Append-only actions
   derive open/under-review/resolved/dismissed state; terminal history is retained.
10. **Allowed actions.** Begin review, add evidence, dismiss with evidence, confirm
    restored artifact, link a uniquely proven existing candidate, mark interrupted
    research blocked, authorize retry through an appended aborted operator attempt,
    cancel eligible Phase 0 work, acknowledge an existing Phase 0 resolution.
11. **Safety/preconditions.** Shared worker lock plus SQLite immediate transaction;
    actor/reason/proof required. Link resolution requires explicit run ownership,
    one completed artifact, exact path and checksum. No arbitrary mutations, guessed
    links, completed-run reopening or automatic finalization. Actions and domain
    changes roll back together.
12. **Interrupted-run handling.** Three-hour age is a review signal only. Evidence
    distinguishes no stored result/artifact, partial or completed report, recorded
    artifacts, unlinked candidates and conflicting evidence. Explicit worker-death
    and partial-effect review remain necessary; no age-based automatic failure.
13. **Phase 0 integration.** Common cases reference operational IDs and proven related
    research IDs. Existing lock and blocked-state rules remain. Cancellation uses
    existing operational record semantics; recorded Phase 0 resolutions can be
    acknowledged without repeating effects. No second scheduler is introduced.
14. **Forecast integration.** Read-only canonical audit composition; authoritative
    revision/output/input identities, candidate checksum, target, fitting cutoff,
    prospective issuance window and recorded source availability checks. No forecast
    authority or revision history changes.
15. **Registry integration.** `registry audit --integrity`; additive schema initialization;
    verifier-only execution context; optional metadata-only existing audit. Earlier
    execution/replay commands retain their semantics. All old tests are unchanged.
16. **Candidate verification.** Report-to-artifact identity, attempt/run ownership,
    model checksum/path/run provenance and missing-link detection. Safe missing-link
    resolution appends a relational link only; model state/bindings are untouched.
17. **Audit additions.** Evidence/status counts, reproduction status counts, case
    states and current typed findings alongside both existing audits. Detection is
    read-only; storing cases is an explicit operation. Deep replay is opt-in CLI work.
18. **CLI/API.** `integrity evidence|verify|replay|history|compare|audit` and
    `integrity reconcile list|detect|show|resolve`, all JSON. Five GET endpoint
    families cover evidence, verification, reproduction history, cases and audit.
    No reconciliation/reproduction HTTP mutation or agent reconciliation authority.
19. **Schema.** Four additive tables: `integrity_reproductions`, `integrity_results`,
    `integrity_cases`, `integrity_actions`; foreign keys, unique request/detection
    identities, append-only triggers, terminal/ordered case constraints and indexes.
20. **Working database migration.** Backup first under the worker lock; before/after
    hashes match for every original table and all 17 registered model files.

    | Preserved data | Before | After |
    | --- | ---: | ---: |
    | Original records | 1,328 | 1,328 |
    | Active bindings | 7 | 7 |
    | Model files | 17 | 17 |
    | Canonical issuances | 22 | 22 |
    | Forecast revisions | 797 | 797 |
    | Forecast migration links | 797 | 797 |
    | Registry runs/specs/attempts/outcomes | 10 each | 10 each |
    | Registry artifacts/model links | 20 / 10 | 20 / 10 |

    Backup: `artifacts/research/research.before-integrity-20260923T012618-54177314.sqlite3`.
    Inventory: `artifacts/research/integrity-migration-verification-20260923T012618-54177314.json`.
    Final audit: `artifacts/research/research-integrity-audit.json`.
    Original inventory was checked again after verification/case writes and still
    matches. No historical evidence was fabricated. Ten legacy verification checks
    are `unavailable`; ten corresponding legacy cases are open, with no resolutions.
21. **Files changed by Phase 1.5.** Added seven `stock_app/research/integrity*.py`
    modules (evidence, audit, CLI, migration, reconciliation, replay, schema),
    `tests/test_research_integrity.py`, and these two research-integrity documents.
    Extended `stock_app/research/{store,__main__,api,registry_admin,registry_cli,
    registry_execution,registry_workflows}.py`. Updated the research platform and
    registry docs and only the roadmap implementation-status paragraph. Other
    dirty files belong to the pre-existing phases and were preserved.
22. **Tests added.** 95 offline tests: manifests/identity, missing/mismatched files
    and datasets, corrupt ownership/metrics/chronology, legacy limits, exact and
    tolerance comparisons, predictions/baselines/model bytes, no new research rows,
    crash/write failures, lock/concurrency behavior, case transitions/deduplication,
    atomic operator/link rollback, forbidden actions, Phase 0 linkage, canonical
    forecast checks, shallow/deep audits, JSON CLI, GET-only APIs, migration safety,
    SQL immutability/uniqueness, and real tiny binary/regression replay.
23. **Final verification.** Baseline: 488 passed. Final full suite:
    **583 passed in 42.69 seconds**. Includes operational, canonical forecast,
    experiment registry, research/evaluation and new integrity tests.
    `compileall`, `git diff --check`, and all three JavaScript scripts
    (`test_chart_data.mjs`, `test_frontend_contract.mjs`, `test_research_frontend.mjs`)
    passed. No live provider was contacted. Current registry audit `ok`; forecast
    ledger `healthy`; integrity `attention` for ten legacy evidence limitations.
    Artifact review: 168 verified checks, 189 unverifiable checks, five not applicable,
    zero missing and zero mismatched. These are check counts, not experiment counts.
24. **Remaining risks.** Real binary/regression replays match numerical reports and
    prediction observations exactly but differ in candidate serialized bytes; this
    is retained as `different`, not silently called equivalent. Historical missing
    preregistration cannot be recovered; ancillary replay remains unavailable.
    Local owner-controlled SQLite/files are not tamper-proof; pickle/joblib remain
    trusted-local formats. Locks do not constrain arbitrary external Python callers;
    file verification has normal TOCTOU limits. Repeated deep reviews/refits have
    storage/CPU costs and require external backup/retention planning. No live-service
    behavior or predictive advantage was established.
25. **Recommended Phase 2 scope.** Separately authorize point-in-time security/source
    identity, raw events and immutable revisions, event/availability/ingestion times,
    vintage queries, and source-bounded SEC/ALFRED ingestion with deterministic
    leakage/replay tests. Preserve current-vintage limitations and existing registry
    links. Do not add an LLM, automatic model promotion or trading as a side effect.
    Phase 2 has not been started.

See [the operator and service contract](research-integrity.md) for command examples,
status semantics, allowed-action preconditions and crash/retry behavior.
