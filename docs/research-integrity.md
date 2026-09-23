# Research integrity, reproduction and reconciliation (Phase 1.5)

Phase 1.5 inspects evidence without rewriting the research it describes. It uses
existing immutable registry contracts, input copies, reports, snapshot checksums,
candidate artifacts and canonical forecast authority. It adds no model math,
selection rules, data provider, LLM, broker, promotion or trading capability.

## Provenance map and evidence gaps

`Question → Hypothesis → Spec → Family/Trials → Run → Attempts → Outcomes`
connects to run input references (snapshot IDs or content-addressed pickle copies),
registered frame hashes, feature/target contracts, code/dependencies, seed and
model configuration. Fit events connect actual trials/checkpoint checksums.
Outcomes reference immutable reports containing folds, selection, baselines,
bootstrap intervals and observation/prediction vectors. Candidate artifact
references connect through `research_model_links` to model records and through
model IDs/checksums to forecast issuances and their authoritative revisions.

Before this phase, the registry audit checked many of these edges, candidate
execution pinned inputs, and `registry replay` verified input/code hashes. That
older replay creates another registered research execution. Forecast replay
already verifies a retained revision without issuing a prediction. Phase 1.5's
`integrity replay` is separate: it creates verification history only.

Remaining gaps are explicit: local files can disappear or change; historical
imports lack preregistration; snapshot metadata is owner-controlled; mutable
model records can disagree with immutable links; interrupted workers can leave
files without results. Ancillary procedures do not have a supported automatic
replay contract. A recent `running` row is not proof of a live worker, and an old
row is not proof of a dead one.

## Evidence manifest

`get_evidence(database, run_id)` returns `{manifest, fingerprint}`. Version
`research-integrity-v1` uses the existing sorted-key, compact, finite-number JSON
serializer and SHA-256. Collections have deterministic ID ordering. It references
stable row IDs and fingerprints; it does not embed datasets, model binaries or
prediction arrays. Immutable outcome/report references provide access to the full
statistical evidence. `contracts` gives explicit value/fingerprint pairs for data,
features, target, randomness, model, evaluation, code, primary/secondary metrics
and comparators. Original execution timestamps remain for chronology checks.

The manifest includes relevant fit events, artifacts, model links and canonical
forecast authority. It excludes inspection time, mutable model lifecycle state,
forecast outcome resolution, incidental duplicate-use events, worker heartbeat,
and reproduction history. The latter is queried separately, avoiding a circular
identity in a reproduction's input manifest. Adding actual provenance changes
(e.g. a new forecast or explicit missing candidate link) changes the regenerated
manifest; earlier reproduction records retain their original manifest fingerprint.
Paths are original evidence locators, not current temporary verifier paths.

## Read-only verification

`verify_run(database, run_id, depth)` uses SQLite `mode=ro`, no runtime
constructor, worker recovery, schema initialization or provider access.

| Depth | Behavior |
| --- | --- |
| `metadata` | Content fingerprints, ownership, declaration linkage, attempt sequence, execution chronology, forecast identity/cutoff/issuance checks. No artifact byte reads. |
| `artifact` | Metadata plus file SHA-256, report readability/result agreement, candidate report/link identity, checkpoint checksums, snapshot raw/adjusted checksums and identity, frame checksums/sample counts. |
| Replay | Explicit `request_reproduction`; performs artifact review, offline execution and structured comparison. It writes only reproduction records/output files. |

Each check has a reference, status, reason and relevant stable IDs:

- `verified`: the stated check actually passed. A verified *contract record* does
  not by itself prove the engine executed that contract.
- `missing`: an expected reference/file is absent.
- `mismatch`: conflicting content, ownership, checksum, metric or chronology.
- `unverifiable`: historical evidence was not recorded, unreadable evidence cannot
  be assessed, or requested depth did not examine bytes.
- `not_applicable`: e.g. the classification threshold for regression.

Overall evidence is `invalid` for missing/mismatched evidence, `incomplete` for
remaining unverifiable checks, otherwise `verified`. Metadata review does not
pretend it verified files. Reports/certificates include timestamp, depth, run,
manifest fingerprint, check details, counts and verifier version. Their canonical
fingerprint is deterministic for the recorded report, including its timestamp;
the same observation at a later time is a different certificate.

## Reproduction records and comparison policy

`request_reproduction(store, run_id, request_key=...)` writes an immutable start
in `integrity_reproductions` and at most one terminal `integrity_results` row.
Starts include original run, request key, mode, tool version, manifest fingerprint,
engine code and environment fingerprints, fixed policy, output path and timestamp.
Terminal rows retain certificate, structured comparisons, reason, status and
completion timestamp. They never create questions, specs, runs, registry attempts,
candidates, model links or forecasts.

A verifier-only context suppresses registry execution/fit hooks while calling the
existing candidate engine. Each request gets a new empty directory under
`integrity-replays/rep-ID`; no old checkpoints are silently reused. Exact pinned
inputs and registered source/dependency versions are required. No network access
or live data fallback exists. Legacy and unsupported ancillary procedures return
`unavailable`; missing inputs are unavailable; contradictory evidence is blocked.

Policy `float64-v1` is fixed in code **before execution** and copied into the start
record. Dictionaries/arrays are traversed with explicit paths. Missing keys,
vector lengths, integers/counts, categorical values, IDs, fingerprints, configuration,
selection and boundaries are exact. Other finite floating values use
`abs(a-b) <= max(1e-12, 1e-10 * max(abs(a),abs(b)))`. There is no caller-defined
or post-result tolerance. Candidate output paths and checkpoint inventory names
are excluded from *result* comparison; original checkpoint bytes are separately
verified. Model artifact SHA-256 remains exact.

Statuses: `exact`, `equivalent` (only predefined numeric roundoff), `different`,
`unavailable`, `failed`, `blocked`. Differences include recorded/replay values,
paths, policy and per-field status. Full report traversal compares metrics,
baselines, bootstrap intervals, observations/predictions, sample counts, split
boundaries, seeds and parameters; candidate bytes are compared separately.
Discrepancy classifications distinguish no discrepancy, expected numeric roundoff,
ambiguous artifact bytes and invalid reproduction. No classification approves a
model or reinterprets historical scientific validity.

**Observed serialization limitation:** tiny real binary and regression replays
match all report measurements and prediction observations exactly while producing
different candidate byte checksums. The engine's JSON configuration round trip
can change Python tuple/list representation in a persisted bundle. The verifier
retains `different` with `ambiguous_artifact_bytes`; it does not loosen the byte
policy, rewrite the original artifact, or claim equivalence of model internals.
The report alone cannot establish that serialization is the only cause.

## Crash/retry and concurrency

A request key is unique and pinned to one run. A per-request OS lock prevents two
processes executing the same request. Completed repeated requests return the same
record. A start without a result remains visible after process death. Once its
lock is free, a repeat of that key appends `blocked`; the operator must inspect it
and use a new explicit key for another check. An active lock returns a busy/blocked
response without appending a false terminal result. Separate explicit keys are
separate verification attempts, never separate research discoveries.

Original evidence is reviewed again after replay. Changed files/history block the
result. A result-write failure leaves the immutable start inspectable; it does not
alter original research. SQLite constraints prevent duplicate terminals and
updates/deletions. External files and SQLite do not share an atomic transaction;
uncommitted replay files remain in the request directory for inspection.

## Reconciliation cases and operator actions

`findings` is read-only. `reconcile detect` explicitly materializes findings into
immutable `integrity_cases`; deterministic detection fingerprints deduplicate
repeated observations. Categories include missing/checksum-invalid evidence,
stale or blocked research, orphan files, incomplete candidate links, unknown
candidate provenance, blocked operational jobs, failed/different/interrupted
reproductions and forecast/legacy ambiguity. Severity and source distinguish
operational failures from research failures, with shared related IDs when proven.

State is derived from immutable ordered `integrity_actions`: initially `open`,
then `under_review`, `resolved` or `dismissed`. Every action stores actor, reason,
evidence, timestamp and resulting state. Terminal cases remain visible and cannot
be silently reopened/deleted. Current findings remain visible independently of
past case disposition, including recurring evidence problems. Detection never
repairs or resolves anything.

| Allowed action | Preconditions/effect |
| --- | --- |
| `begin_review` | Append `under_review`. |
| `manual_evidence_added` | Nonempty supporting evidence; remains under review, does not rewrite the run. |
| `dismissed_false_positive` | Explicit actor/reason/evidence; terminal dismissal only. |
| `confirmed_existing_artifact` | Registered artifact case; current bytes must match its original checksum. No file repair performed. |
| `linked_existing_candidate` | Explicit model run ID, exactly one completed candidate artifact, identical path/checksum and no existing model link. Append only the missing link. |
| `run_marked_blocked` | Interrupted case, unfinished latest attempt, worker lock free and explicit worker-death confirmation. Append blocked outcome; case stays under review. |
| `retry_authorized` | Latest attempt blocked; worker-death and partial-effects review explicitly supplied. Append the existing registry-style operator reconciliation attempt/aborted outcome; it does not execute the retry. |
| `operational_cancelled` | Phase 0 blocked/failed execution, no live referenced legacy job; same worker lock/state constraints as Phase 0. Preserve its reconciliation metadata and append common action atomically. |
| `confirmed_operational_resolution` | Existing Phase 0 succeeded/cancelled status with explicit recorded reconciliation; reference it without repeating effects. |

All action commits acquire `worker.lock` and `BEGIN IMMEDIATE`; permitted domain
writes and action history commit together. Failures roll back both. Actor and
reason are required; no arbitrary SQL or arbitrary field mutation is exposed.
Completed runs cannot reopen. Ambiguous candidates cannot be selected by filename
or timestamps. There is no artifact-based automatic finalization and no new
success-marking path for ambiguous operational effects: use the existing Phase 0
operator process and then acknowledge its recorded result.

Stale policy `age-3h-review-only-v1` flags open attempts older than three hours.
It classifies no recorded artifact/result, recorded artifacts or an unlinked
candidate, retaining related job/attempt IDs. Age does not steal a lock or fail a
run. Even a valid orphan result remains review evidence. Before marking blocked,
operators must stop direct/manual execution as well as confirm worker death;
unmanaged Python callers need not honor the shared worker lock.

## Audit, CLI and read-only API

The integrity audit composes the **existing** registry and canonical forecast
audits, evidence counts, reproduction counts, case states and current findings.
Default metadata audit avoids file hashing and all replay. Artifact depth checks
files. An explicit CLI `--replay` batch writes reproduction checks in addition to
the read-only audit; the audit service itself remains read-only. Listing runs
never invokes expensive replay.

All commands print JSON:

```bash
python -m stock_app.research integrity evidence RUN_ID
python -m stock_app.research integrity verify RUN_ID --depth metadata
python -m stock_app.research integrity verify RUN_ID --depth artifact
python -m stock_app.research integrity replay RUN_ID --request-key operator:check-001
python -m stock_app.research integrity history RUN_ID
python -m stock_app.research integrity compare RUN_ID REPRODUCTION_ID
python -m stock_app.research integrity reconcile detect
python -m stock_app.research integrity reconcile list
python -m stock_app.research integrity reconcile show CASE_ID
python -m stock_app.research integrity reconcile resolve CASE_ID \
  --action begin_review --actor 'local operator' --reason 'Inspecting interrupted worker evidence'
# --evidence evidence.json supplies an object for actions requiring explicit proof.
python -m stock_app.research integrity audit
python -m stock_app.research integrity audit --depth artifact
python -m stock_app.research integrity audit --depth artifact --replay --request-key operator:batch-001
python -m stock_app.research registry audit --integrity --depth artifact
```

Place `--database PATH` immediately after `integrity` or `registry`. Existing
registry and research replay commands retain their old execution semantics;
use **integrity replay** for verification without new discovery history.

GET-only endpoints under `/api/research/integrity`:

- `/runs/<run_id>/evidence`
- `/runs/<run_id>/verification?depth=metadata|artifact`
- `/runs/<run_id>/reproductions`
- `/cases` and `/cases/<case_id>`
- `/audit?depth=metadata|artifact`

No reproduction or reconciliation mutation is exposed over HTTP. Existing
same-origin mutation protection is not a strong operator authentication system.

## Storage migration and future-agent boundary

Four additive tables (`integrity_reproductions`, `integrity_results`,
`integrity_cases`, `integrity_actions`) use foreign keys, uniqueness, state checks,
indexes, append-only triggers and ordered terminal case transitions. Existing
SQLite schema compatibility/version and canonical authority remain unchanged.
`python -m stock_app.research.integrity_migration DATABASE` takes the shared lock,
backs up SQLite before installation, hashes all original table rows and registered
model files before/after, and emits an inventory report. Installation is
idempotent/restart-safe. Back up external artifacts separately as well.

A future agent can receive only `get_evidence`, `verify_run`,
`request_reproduction` and read-only `findings`/history. It must not receive
`resolve`, raw store/SQL access, arbitrary Python, artifact writes, spec mutation,
metric replacement, deletion or model activation. No agent is implemented here.
The Python module boundary is an application design, not a sandbox against a
caller already granted arbitrary Python or local owner access.

Local owner-controlled SQLite/files are not tamper-proof. File checks have normal
TOCTOU limits; before/after replay verification detects common concurrent changes,
not adversarial replace-and-restore attacks. Pickle/joblib inputs remain trusted
local evidence, never untrusted uploads. History and replay files need external
backup/retention; no automatic deletion policy is added. Legacy incompleteness is
not historical invalidation or proof of forecast performance. Phase 2 should
address point-in-time identity, availability/vintages, raw-event provenance and
SEC/ALFRED ingestion as a separately authorized scope; none begins here.
