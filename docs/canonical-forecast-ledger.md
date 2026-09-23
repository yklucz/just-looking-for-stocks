# Canonical forecast ledger (Phase 0.2)

A forecast is now one logical issuance with immutable child input revisions.
Previously the forecast ID hashed `[model_id, snapshot_id, origin, horizon]`.
Refreshing inputs changed the snapshot hash and created another top-level forecast
for the same decision. Qualification worked around this by selecting the first
row per origin, while lists and storage continued growing as if these were
independent forecasts.

The path is now:

```text
source -> checksum-verified immutable snapshots -> unchanged model inference
       -> canonical issuance + immutable input revision (one SQLite transaction)
       -> flattened authoritative forecast for API/list/qualification
       -> explicit revision history and offline replay for audit
```

Phase 0 scheduling, retries, freshness and blocked/reconciliation behavior remain
in place. No model fitting math, active selection, artifacts, promotion criteria,
research agent, trading, SEC or ALFRED integration is changed.

## Identity contract

The canonical ID is `fc-` plus SHA-256 of deterministic, sorted JSON containing:

| Identity field | Meaning |
|---|---|
| `version` | Canonical identity contract version 1 |
| `model_id` | Existing immutable registered model/version |
| `model_artifact_sha256` | Registered model bytes fingerprint, when available |
| `model_contract_fingerprint` | Full legacy binding fingerprint, or persisted candidate metadata fingerprint excluding its filesystem path |
| `model_family` | Family from the persisted model contract |
| `security` | Explicit namespace, identifier, symbol and XNYS exchange |
| `origin` | Exchange-session date; an input session label, not a UTC date-shifted instant |
| `horizon` | Positive count of exchange sessions |
| `frequency` | `1d`; other frequencies are rejected by this daily ledger |
| `target_definition` | Task, adjusted-Close log-return measure, event operator and threshold |

The repository has no permanent security master. Current callers use the explicit
`symbol` namespace; ticker renames/reuse are **not** silently resolved. An explicit
permanent ID can be supplied but does not manufacture a security-master mapping.
The full artifact/version identity prevents a family name or truncated legacy
model ID alone from conflating different models.

Input snapshot IDs, refresh times, issuance timing/kind, output values and current
model lifecycle state are not identity fields. A late replay of the same decision
is a child revision, not another issuance. Economic contract changes produce
another identity; changes conflicting with an existing immutable registered
contract are rejected. Existing registered forecasts retain the five-session,
0.002 threshold semantics. The low-level writer retains its historical defaults
for callers without a registry contract and records unavailable fingerprints as
null; migration never uses those defaults to guess historical identity.

## Issuance, revision and evaluation fields

`forecast_issuances` stores the canonical identity and compatibility metadata:
symbol, model, origin, target session, horizon, initial issuance/recording times,
kind, state and one authoritative revision pointer. Identity columns and the
pointer are immutable. JSON identity uniqueness is enforced in SQLite in addition
to the deterministic primary key.

`forecast_revisions` stores the input key, snapshot/map lineage, original payload,
output checksum, generation (`issued_at`) and recording (`created_at`) timestamps,
revision kind/reason, previous revision ID, and available model/feature/source
provenance. Source timestamps and dataset hashes are copied only when persisted
metadata exists. The original snapshot mapping remains available even when source
metadata is unavailable. Updates and deletes are rejected by SQLite triggers.

An input key hashes the exact snapshot ID, snapshot mapping and legacy input
checksum. The revision ID is `fr-` plus a hash of issuance ID and input key.
`UNIQUE(issuance_id,input_key)` prevents equivalent retries from creating another
revision. Different snapshot IDs can create different child revisions, even when
their data happen to match; both input lineages are retained. They cannot create
another canonical issuance. This phase does not compact byte-equivalent datasets.

The same exact input identity with conflicting prediction content raises a
reconciliation error instead of silently replacing output or adding a second
revision. Output hashes exclude changing health observations and redundant
origin/target/generated timestamps, not prediction values. Historical duplicate
raw rows mapping to one equivalent revision remain separately retained and mapped.

Evaluation metadata (`state`, realized `outcome`, and `outcome_revisions`) belongs
to the issuance, separate from immutable prediction revisions. Existing outcome
correction behavior remains, using the authoritative prediction's original price
basis. The canonical store rejects attempts to replace prediction/identity fields
through the generic update API.

## Authoritative and causal policy

**The first accepted revision remains authoritative permanently.** There is no
"latest prediction wins" pointer update. Later inputs remain queryable for audit,
current inference responses and replay, but cannot replace the prediction used
for prospective scoring. A revision generated after the next session opens is
labeled historical replay. A replay-first issuance cannot become prospective
through a later revision or a backdated retry.

For new writes, first accepted means first successful serialized SQLite commit;
concurrent callers cannot choose authority by racing a later pointer update. For
migration it means earliest persisted original `created_at`, normalized to UTC.
Conflicting predictions tied for earliest ordering make the group ambiguous.
This is deliberately conservative if a replay was recorded before an on-time
prediction for the same decision.

Qualification reads the canonical flattened authoritative payload once per
issuance and retains its existing first-origin, matching, 126-origin and metric
rules. Revisions do not add observations. Unmigrated low-level legacy records
retain the old first-origin adapter until explicit migration; quarantined
ambiguous rows are excluded. The standard API/export only returns canonical
issuances, so migrate existing databases before switching readers.

## Transaction and retry behavior

`BEGIN IMMEDIATE` serializes concurrent writers. Issuance, immutable revision and
authoritative pointer commit in one transaction. The pointer is non-null, and a
deferred composite foreign key requires it to belong to that issuance. The
revision also references its parent. An interrupted/failed insert cannot leave a
committed empty issuance or orphan revision. There is no separate non-atomic
"publish current pointer" step.

Repeated identical requests return the same issuance/revision IDs. Research
runtime and active-candidate inference reuse existing outputs when exact inputs
match and both output and current artifact integrity are verified. Inputs are
still checksum-verified before reuse. The legacy public prediction pipeline still
performs its existing computation, but its persistence is deduplicated. Prediction
math is unchanged.

Phase 0 forecast jobs now record both `forecast_ids` (canonical IDs) and
`revision_ids`. Repeating the deterministic forecast service after a lost
acknowledgement cannot duplicate completed model/input writes. Multi-model batches
can still partially succeed, and non-ledger services write outside this
transaction. Their operational run remains blocked under Phase 0 until reviewed;
this phase does not broaden automatic retry authority or redesign the scheduler.

## Migration and recovery

Run from the repository using the same persistent root as the application:

```bash
.venv/bin/python -m stock_app.research forecast-audit
.venv/bin/python -m stock_app.research migrate-forecasts
.venv/bin/python -m stock_app.research forecast-audit
```

All ledger administration commands accept `--database /absolute/path/research.sqlite3`.
`STOCK_RESEARCH_ROOT` otherwise selects the root. Stop old-version application
processes before switching versions so they cannot keep writing the old ledger.
The migration command takes the Phase 0 worker lock and makes a SQLite-consistent
`*.before-canonical-*.sqlite3` backup **before** schema/migration writes. It does not
construct the model-importing application runtime.

Migration adds `forecast_issuances`, `forecast_revisions`, and `forecast_migration`,
indexes and immutability triggers. It retains research schema version 1 and leaves
every original forecast document in `records` unchanged. Model records, active
pointers, datasets and model files are not rewritten. Original records can be
read with old IDs or exported with `forecast-legacy`.

Historical grouping requires persisted model/task/target/frequency/version proof,
valid target sessions and timestamps, and consistent predictions for equivalent
inputs. Persisted legacy contract fingerprints and candidate artifact identity
must agree with registry fields. Missing evidence is not inferred from today's
model files. A possibly related ambiguous row blocks the entire model/security/
origin group, including when its horizon is missing. The audit reports each
ambiguous legacy ID and reason; nothing is merged by guessing.

All grouping/mapping writes commit in one transaction. A crash rolls back and can
be retried. Already mapped or quarantined originals are checked against retained
hashes on every rerun; unchanged rows are skipped. A changed original stops the
migration for investigation. Quarantined groups are not automatically reconsidered
or merged after metadata edits. Preserve their originals, inspect registry and
source evidence, and resolve them through a separately reviewed adjudication;
this phase intentionally supplies no guess-based automatic reconciliation.
New writes for an unmigrated/ambiguous legacy origin are blocked.

Migration statistics report examined rows, new canonical issuances, collapsed
duplicate groups, new revisions, ambiguous groups/rows and unchanged rows. The
read-only audit reports totals, ownership/cardinality/fingerprint violations,
unmigrated rows and reconciliation details.

Rollback: preserve the pre-migration backup and all external artifacts. Old code
can still read the retained legacy rows, but does not see forecasts issued only
in canonical tables. A code downgrade is therefore not a complete ledger rollback.
Do not mix old/new writers; retain the new database for evidence and reconcile any
post-upgrade records before restoring a backup. There is no destructive down
migration or historical deletion command.

## Query, audit and replay interfaces

```bash
.venv/bin/python -m stock_app.research export --kind forecasts
.venv/bin/python -m stock_app.research forecast-revisions fc-ISSUANCE_ID
.venv/bin/python -m stock_app.research forecast-replay fc-ISSUANCE_ID fr-REVISION_ID
.venv/bin/python -m stock_app.research forecast-legacy
```

- `GET /api/research/forecasts` and forecast exports return one canonical row with
  its authoritative payload, preserving existing list/filter/CSV fields.
- Rows expose stable `id`, `revision_id`, `authoritative_revision_id` and identity.
- `GET /api/research/forecasts/<id>/revisions` returns explicit child history.
- `GET /api/research/forecast-ledger/audit` exposes the read-only audit service.
- `issue_forecast()` returns the requested revision's flattened payload/kind for
  generation-call compatibility, with the stable canonical `id`, separate
  `revision_id` and `canonical_kind`. Its deprecated `revision_of` identifies the
  parent issuance; `previous_revision_id` on revision history is the precise chain.
- Generic `ResearchStore.list('forecasts')` retains an unmigrated-original fallback
  for legacy callers. After migration mapped/ambiguous originals are excluded;
  the normal API/export never exposes originals as extra logical issuances.

Canonical list retrieval uses one indexed join, not per-forecast revision queries.
The audit opens SQLite read-only without initializing schema/runtime. Missing
schema is reported as migration required, not created by an audit. It checks
identity uniqueness, equivalent revisions, revision ownership, authoritative
pointers, prospective authority, fingerprints, retained originals and foreign
keys. Multiple authoritative pointers are structurally impossible because the
issuance has one immutable scalar pointer.

Replay loads the recorded snapshot mapping (or a retained legacy input CSV),
verifies checksums and the recorded model version, and runs the existing estimator
without issuing records, fitting models or contacting providers. It compares the
recorded prediction values exactly. Missing artifacts/lineage fail explicitly;
provenance does not imply that missing source bytes can be reconstructed.

## Verification and next boundary

Offline tests exercise duplicate/concurrent writes, identity changes, same-input
conflicts, partial initial/later writes, transaction rollback/retry, migration
restart and ambiguity, original-history retention, SQL invariants, read-only
audit, canonical API output, revision history, cache reuse, operational retries,
replay and evaluation causality. A 126-origin regression test confirms identical
metrics after adding perfect but late revisions.

Phase 1 should introduce a Research Experiment Registry: immutable registered
hypotheses/specifications, trial-family/parent relationships, input/config/code
fingerprints, declared metrics/stopping rules and append-only attempt/outcome
history. It should account for all attempted experiments before any agent proposes
new ones. That work is outside Phase 0.2 and has not started.

## Local migration verification — 2026-09-22

A rehearsal on a SQLite backup copy completed first; a second pass created no
issuances or revisions and reported all 797 originals unchanged. The working
local database was then migrated with the same results:

| Measure | Result |
|---|---:|
| Historical rows examined and retained | 797 |
| Canonical issuances produced | 22 |
| Duplicate logical groups collapsed | 6 |
| Immutable revisions retained | 797 |
| Ambiguous groups/rows | 0 |
| Audit integrity/cardinality violations | 0 |

Pre-migration backup:
`artifacts/research/research.before-canonical-20260922T122057-928a6ed7.sqlite3`.
All original `records` documents were fingerprint-compared before/after, including
forecast history. The seven active bindings and all 17 registered model files
were also verified unchanged. Four offline replay checks (legacy/candidate ×
binary/regression) matched retained prediction values exactly, using existing
local snapshot/model bytes. These checks do not establish forecast accuracy or
live-provider behavior. The backup and research artifacts remain Git-ignored.
