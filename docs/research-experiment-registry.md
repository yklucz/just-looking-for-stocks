# Immutable research experiment registry — Phase 1

Implemented and verified locally on 2026-09-23. This is research-control metadata around the existing deterministic Python engines. It adds no agent, autonomous proposal generation, data provider, trading capability, production eligibility rule, or model promotion.

## Existing workflow and the gap

Candidate experiments enter through `python -m stock_app.research experiment`, the research jobs API, direct `run_experiment`, replay, or Phase 0 monthly binary/regression jobs. The runtime pins immutable snapshots and invokes the nested chronological engine. `ExperimentConfig`, feature/target contracts, and the engine manifest already described computations. Fold JSON files, checksummed fit checkpoints, candidate bundles, and a final report recorded successful work. The mutable job record retained only its latest failure/progress state. Direct calls could produce a candidate without any persistent database experiment identity.

Binary selection uses validation log loss, then sigmoid calibration; held-out primary comparisons use Brier loss. Regression selection uses validation mean squared error; held-out primary comparisons use MAE and separate quantile intervals. These calculations, raw-origin purges, baseline computations, bootstrap implementation, and candidate/shadow/active/retired lifecycle are unchanged.

## Architecture and ownership

`registry.py` supplies typed registration, run, attempt, result, and reconciliation services. `registry_schema.py` adds eleven `research_*` tables to the existing research SQLite database. `registry_execution.py` wraps candidate computation, records fits, freezes inputs, and implements verified offline execution/replay. `registry_workflows.py` wraps the ancillary single-split, walk-forward, GRU, and feature-addendum public entry points. `registry_admin.py` provides historical import and a genuinely read-only audit. `registry_cli.py` exposes local administration; API additions expose reads only.

```text
Question → Hypothesis → Spec → logical Run → numbered Attempts → terminal Outcomes
               ↓         ↓                        ↓                 ↓
             Family → parameter Trials        fit Events       Artifact references
                                                                   ↓
                                                          model provenance Links
```

All existing record kinds, active bindings, and canonical forecast tables retain their original semantics. Registry schema installation is additive and idempotent. The existing database version remains compatible; table existence identifies availability of this additive feature.

## Entities, identity, and immutability

| Entity | Meaning and identity | Mutation policy |
| --- | --- | --- |
| Question | Stable content identity, title, description, creator, timestamp, initial status, optional parent | Immutable; create a child to revise scope |
| Hypothesis | Parent question, statement, expected effect, rationale, falsification criterion, fingerprint | Immutable immediately; revised meaning requires a new identity |
| Family | Hypothesis plus declared dimensions and allowed values | Immutable; allowed values canonicalized as sets |
| Spec | Hypothesis/family semantic fingerprints plus the frozen execution contract | Immutable immediately; unique fingerprint |
| Trial | A complete allowed parameter realization; unique fingerprint within its family | Immutable; all declared candidate variants are registered before computation |
| Run | Spec, intended execution key, origin, exact input references, output location | Immutable identity; status is derived from append-only attempts/outcomes |
| Attempt | Run, monotonic attempt number, start timestamp, explicit resume metadata | Append-only |
| Outcome | One terminal record per attempt, declared metric identity, results, disposition, evidence, rule/version | Append-only; cannot replace prior results |
| Event | Proposal, duplicate execution, fit start/completion/failure, validation score, or annotation | Append-only |
| Artifact | Run/attempt, role, byte checksum, local path | Immutable reference; audit verifies actual bytes |
| Model link | Existing model ID → originating run and candidate artifact | Append-only; foreign keys prohibit nonexistent model/run/artifact targets |

Nonempty question and falsification fields use deterministic minimum-length validation, not LLM interpretation. This does not establish scientific merit. Failed proposals that never pass registration cannot start evaluation. Duplicate valid proposals reuse identities and append registration events, so proposals do not disappear.

Fingerprinting reuses `forecast_identity.canonical_json` and `digest`: sorted dictionary keys, compact JSON, finite numeric values, SHA-256. Timestamps and database IDs are stored outside specification identity. Paths, output directories, job/run IDs, and creation/update timestamps are rejected inside contracts. Parent relationships use semantic fingerprints, not arbitrary row IDs. Snapshot IDs/paths are retained as run provenance; candidate spec data identity uses the existing `_frame_hash` and exact date/row coverage. Context frames and the primary history have separate identities, including when a context uses the same symbol.

Allowed-value ordering does not change family identity. Engine search ordering remains in `ExperimentConfig`, because tie-breaking can depend on that ordering. Output path participates only in the default direct-call execution key, never the spec fingerprint. Source identity hashes relevant model, feature, target, training, evaluation, configuration, and experiment/statistics files by repository-relative name; Python and numerical-library versions are recorded. Unrelated Git dirty state and volatile timestamps are excluded.

## Preregistration contract

Before a candidate engine call starts, the registry freezes:

- Hypothesis and trial-family relationships, binary/regression task, security, exact history/context content fingerprints.
- Full `ExperimentConfig`, explicit raw outer-fold origin/label boundaries when valid, validation/calibration sizes, purge horizon, selection metric, equal-fold aggregation.
- Feature sets and `FeatureConfig`; adjusted-close log-return target, event threshold, horizon.
- XGBoost procedure and all feature/window/parameter alternatives; optional GRU request and source-version identity.
- Binary primary **Brier** or regression primary **MAE**; declared secondary metrics, named baselines, paired moving-block bootstrap configuration (20-session block, 95% interval, configured resamples/seed).
- Seed, estimator/early-stopping limits, invocation time budget and operator cancellation policy, descriptive disposition rule, source and dependency identities.

Insufficient history is recorded as unavailable boundary information; the existing engine then validates it and its failed/invalid attempt remains visible. Registration does not quietly shrink the experiment. Changed contracts require a different spec. A job execution key pinned to a different spec is rejected rather than silently repointed.

The built-in question/hypothesis wording is a fixed description of the existing comparison workflow, not autonomous hypothesis invention. Users can register their own falsifiable question/hypothesis/family and an exactly supported contract, then execute it through `execute_spec`. The service checks that actual inputs/configuration/code match that contract before evaluation. Arbitrary registered contracts are not instructions to execute arbitrary Python or SQL.

## Families, fits, and multiple-testing groundwork

Candidate family dimensions are `feature_set`, `window`, and complete XGBoost `parameters` dictionaries. The settings dimension intentionally preserves the declared correlated settings rather than inventing a Cartesian product of individual hyperparameters. Every supported combination is registered before evaluation. Each checkpoint fit records its scope/key, attempt, trial (for tuning fits), cache status, outcome, and checksum. Selection scores are separate events. Ancillary model fitting records model type/configuration and failures through the existing partitioned-fit adapter.

A failed fit remains visible even if no report can be written. An unattempted variant remains distinguishable from an attempted variant. The audit counts declared and attempted distinct variants per family; event chronology and attempt numbers support counting repeated searches under each hypothesis. Reused checkpoints are marked as cached, not new independent discoveries. No multiple-testing correction or statistical independence claim is introduced.

## Runs, retries, state, and crash consistency

```text
registered → running → completed | failed | invalid | aborted | blocked
                          ↑
              explicit resume after failed/invalid/aborted
```

State is derived centrally, not independently updated in several tables. SQL forbids an additional attempt while one is unfinished, nonsequential attempt numbering, duplicate terminal outcomes, and attempts after completion. Ordinary service calls also reject retries from blocked state. A completed call with the same execution identity verifies the successful attempt's artifacts and returns its immutable report without recomputation or a new attempt.

Runtime/Phase 0 keys are `job:<durable job id>`. A scheduled retry therefore belongs to the same logical run, with another numbered attempt only when retry is permitted. The scheduler itself retains Phase 0 locks, effects boundary, retries, and reconciliation rules. Its output additionally identifies the research run/spec. Direct candidate calls default to a key derived from output directory plus spec fingerprint; a different output or explicit execution key requests a distinct execution. Ancillary explicit invocations each create a new execution under a deduplicated spec.

Cancellation and exhausted time budgets produce `aborted` outcomes with the exact engine `cancelled`/`paused` status retained. Validation errors produce `invalid`; other computation errors produce `failed`. An interruption during artifact/result persistence produces `blocked` when SQLite remains reachable. If the process/database dies before that record can commit, the open attempt stays `running` and the audit exposes it. Neither case permits an automatic rerun.

Results, runtime candidate registration, artifact references, and candidate provenance links commit in one transaction. A link failure rolls everything back; a separate blocked outcome references uncommitted artifacts. Model files are never deleted to hide failures. Atomic content-addressed, checksummed registry input/report files survive independently; orphan reports and candidate output files are audited. A report that already says completed before a new candidate execution is refused, preventing retrospective output from masquerading as fresh preregistered evaluation.

Reconciliation is an explicit local operator action with actor/reason. First close an interrupted running attempt as blocked after confirming no worker owns it. Then explicitly reconcile blocked work to an appended aborted resolution attempt before resuming. The resolution attempt is labeled as reconciliation, not a model computation. No result is guessed from an orphan file. A completed run cannot be reopened; use a distinct execution key for another experiment execution.

## Results, primary metrics, and dispositions

Each outcome retains the preregistered primary metric identity, scalar aggregate primary/secondary values where available, separately labeled exploratory values, comparison evidence, rule/version/source, and immutable report/artifact references. Full reports retain fold/sample counts, periods, exclusions, baseline metrics, confidence intervals, observations, selected parameters, and calibration details; the registry does not duplicate those large arrays in SQL. Missing measurements are explicit, not converted to zero.

Candidate disposition rule `descriptive-baseline-direction-v1` interprets the engine's existing paired-bootstrap outputs against `training_prior` (binary) or `unchanged_price` (regression):

- `not_supported`: nonpositive mean outer-fold improvement.
- `supported`: every outer-fold lower confidence bound is above zero.
- `inconclusive`: other completed outcomes, including unavailable comparison evidence.
- `invalid`, `failed`, `aborted`, `blocked`: the corresponding non-completed states.

This is a descriptive registry label, not a new estimator, statistical test, production gate, or multiple-testing-adjusted conclusion. It never changes selection, qualification, shadow state, activation, or retirement. All previous terminal attempts, including negative outcomes, remain in default history queries.

Ancillary single-split/walk-forward procedures preserve per-model/per-fold metric locations in the immutable report rather than manufacture a scalar aggregate. Their evidence-only disposition is inconclusive. Direct GRU records its available aggregate Brier metric. Feature addenda are explicitly `exploratory`, linked to the original report checksum, and have their own immutable contract; they cannot rewrite the original primary metric/result. They retain source task, target, horizon and configuration. Automatic registry replay is supported for the candidate engine; ancillary callbacks require the original explicit invocation.

## Data and model provenance

Runtime jobs retain existing verified snapshot IDs and content hashes. Direct DataFrame calls store content-addressed local input copies with byte and frame checksums. The copies are local trusted pandas pickle data for exact dtype/index replay; they must not be accepted from an untrusted agent or external upload. Registry replay verifies file/snapshot integrity, frame content, source and dependency versions before computation. It loads no live provider.

Candidate bundles are not rewritten. Runtime candidates gain nullable-compatible metadata links and a separate relational provenance link, committed with their result. Previously registered versions retain their existing state and metadata; a link can be added without altering them. Direct candidate calls have artifact-to-run provenance even when no dashboard model record exists. Historical models without evidence remain valid, without fabricated hypotheses or experimental results.

## Historical migration and verified local statistics

`import-legacy` examines persisted experiment jobs and only links models whose stored `metadata.job_id`, actual artifact bytes, and stored checksum agree. It preserves an immutable source-job evidence copy and its checksum, original task/config/fingerprint/snapshot evidence, and final recorded state. It never invents a historical question, hypothesis, primary metric, preregistration, or missing earlier attempt. Legacy rows use `registration_mode=legacy_import`; metrics remain historical/exploratory evidence. Ambiguous model links are reported, not guessed. Importing twice is idempotent.

Local application on 2026-09-23:

| Check | Observed result |
| --- | --- |
| Source experiment jobs / imported runs | 10 / 10 |
| Candidate links / ambiguous links / missing evidence | 10 / 0 / 0 |
| Repeat import | 0 new, 10 already registered |
| Registry audit | `ok`, zero issues |
| Legacy question/hypothesis/primary-metric invention | None |
| Original records | All 1,328 unchanged |
| Active bindings / model files | All 7 bindings and 17 files unchanged |
| Canonical forecasts | 22 issuances, 797 revisions and 797 migration links unchanged |

Backup: `artifacts/research/research.before-experiment-registry-20260923T004742.sqlite3`.
Verification evidence: `artifacts/research/experiment-registry-migration-verification.json`.
All original table contents and model bytes were hashed before/after under the shared worker lock. These local ignored artifacts are not a new source-controlled model release.

## CLI and read-only API

Existing `experiment`, `resume`, and `replay` commands continue to work and register their research. Additional examples:

```bash
python -m stock_app.research registry history
python -m stock_app.research registry list --kind specs
python -m stock_app.research registry show --kind families FAMILY_ID
python -m stock_app.research registry show RUN_ID
python -m stock_app.research registry list --kind events
python -m stock_app.research registry list --kind model_links
python -m stock_app.research registry audit
python -m stock_app.research registry import-legacy          # preview
python -m stock_app.research registry import-legacy --apply
python -m stock_app.research registry register-question question.json
python -m stock_app.research registry register-hypothesis hypothesis.json
python -m stock_app.research registry register-family family.json
python -m stock_app.research registry register-spec spec.json
python -m stock_app.research registry register-trial trial.json
python -m stock_app.research registry execute SPEC_ID --inputs inputs.json --execution-key manual:explicit-001 --output /tmp/research-run
python -m stock_app.research registry replay RUN_ID --output /tmp/research-replay
python -m stock_app.research registry reconcile RUN_ID --actor 'local operator' --reason 'Worker confirmed dead and artifacts reviewed'
```

Place `--database PATH` immediately after `registry` to select another database. Registration JSON uses the typed service keyword fields. A question example is `{"title":"Test volatility features","description":"Compare the declared volatility feature procedure against its baseline","creator":"local user"}`. A hypothesis supplies `question_id`, `statement`, `effect`, `rationale`, `falsification`. A family supplies `hypothesis_id` and `dimensions`; a spec supplies `hypothesis_id`, `family_id`, and a full `contract`; a trial supplies `family_id` and `parameters`. `prepare(registry, history, options)` returns a supported frozen spec, exact input references, and trials without evaluating; this is the practical way to obtain the full candidate contract. `execute` accepts those references or `--inputs-from RUN_ID`. No command accepts arbitrary executable code or SQL.

Read-only endpoints:

- `GET /api/research/registry/history`
- `GET /api/research/registry/{questions|hypotheses|families|specs|trials|runs|attempts|outcomes|events|artifacts|model_links}`
- `GET /api/research/registry/{kind}/{id}` (entities with a single ID)
- `GET /api/research/registry/audit`

They open SQLite in read-only mode and do not initialize a runtime, activate legacy bindings, start workers, or recover attempts. History includes all states. Registry mutation endpoints are not exposed.

## Audit and verification

Audit checks/counts all entity types, terminal/nonterminal state, duplicate spec fingerprints, foreign keys, content-fingerprint drift, outcome primary metrics, unfinished fits/attempts, missing/changed inputs and artifacts, orphan result/candidate files, uncommitted artifacts, candidates lacking provenance, legacy imports, and declared/tested family variants. An unfinished attempt may still be live; audit reports it and never decides recovery. Inspection never mutates state.

Verification comprises 70 new focused offline tests plus the unchanged 418-test baseline (488 total), compilation, whitespace checks, and all three JavaScript regression scripts. Focused tests cover SQL immutability/constraints, canonicalization, concurrency, duplicate proposals/trials/executions, invalid transitions, attempts/reconciliation, negative/invalid/failed/aborted outcomes, exploratory metrics, transactional link failures, orphan audits, legacy import, real tiny binary/regression execution, exact replay, explicit custom-hypothesis execution, and Phase 0 scheduled integration. No external providers or active-model changes are part of these tests.

## Limitations and Phase 1.5 integration

This is a local audit/control boundary, not tamper-proof storage against a database owner who drops triggers or edits files. Python callers with arbitrary execution privileges can bypass public entry points; the future agent must have only typed control-service access. Scientific hypothesis quality is not judged by string validation. Statistical dispositions remain unadjusted research summaries, not investment evidence. Data retains current-vintage/source limitations; historical missing attempts cannot be reconstructed. Ancillary custom callback globals/native state are explicitly not fully captured, and those procedures do not have automatic registry replay. Interrupted workers require operator reconciliation. Content-addressed evidence needs backup/retention planning; no deletion/garbage collection policy is implemented.

Phase 1.5 now adds a separate [research integrity layer](research-integrity.md): deterministic manifests, read-only evidence reviews, append-only verification reproductions, and human reconciliation cases/actions. `integrity replay` never creates another ExperimentRun; the older `registry replay` retains its distinct-execution semantics. `registry audit --integrity` composes both existing audits without replay by default. Existing reconciliation remains compatible; common cases can record narrow transactional resolutions and acknowledge Phase 0 decisions. No autonomous proposals, promotion, new ingestion or trading are introduced.


## Optional PIT provenance (Phase 2A)

New specifications may freeze `contract.data.pit_manifest` produced by the
[point-in-time query service](point-in-time-data.md). The registry validates it and
requires matching run provenance. Existing experiments retain their original
current-vintage/legacy evidence. No built-in model workflow consumes PIT inputs yet.
