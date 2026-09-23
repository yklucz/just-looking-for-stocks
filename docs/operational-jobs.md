# Durable operational jobs (Phase 0)

Recurring research execution now runs independently of Flask:

```text
cron / launchd / hosted trigger
    -> python -m stock_app.jobs run-due
    -> shared worker.lock -> SQLite schedules, runs, attempts
    -> prepare and validate inputs -> persist effect fence -> application service
    -> succeeded / retry / failed / blocked -> heartbeat and source health
```

This is a finite CLI invocation, not another required daemon. The existing
macOS/Linux filesystem lock and SQLite WAL database are reused. There are no new
package dependencies. No LLM, broker, model promotion, evaluation-rule change,
is included. Forecast-ledger canonicalization was added separately in
[Phase 0.2](canonical-forecast-ledger.md); forecast run outputs now include stable
canonical `forecast_ids` and the exact `revision_ids` used. Phase 0's conservative
blocked/reconciliation behavior is unchanged.

## Registered services and clocks

- `refresh:SYMBOL`: daily Yahoo OHLCV/context ingestion, validation, immutable
  snapshot registration, and existing forecast-outcome resolution.
- `forecast:SYMBOL`: daily existing active/shadow issuance using pinned, fresh
  source snapshots. Partial model failures are visible blocked runs.
- `experiment:SYMBOL:binary` and `experiment:SYMBOL:regression`: monthly existing
  candidate experiment service; same inputs, settings, evaluation and candidate
  registration as before. No automatic nomination or activation. GRU is excluded.

The registry reads the existing universe setting (initially AAPL, SPY, NVDA,
TSLA), plus all configured context ETFs. `status` lists exact registered names.
Operational CLI startup does **not** import or activate legacy model bindings.

Daily slots are XNYS session close plus 30 minutes, including holidays and early
closes. Monthly slots are the first exchange session of each month at that time.
All persisted execution timestamps are UTC. Production uses the real clock;
tests inject a clock and provider and never sleep or contact a network.

## CLI

From the repository, using its virtual environment:

```bash
.venv/bin/python -m stock_app.jobs run-due
.venv/bin/python -m stock_app.jobs run refresh:AAPL
.venv/bin/python -m stock_app.jobs run forecast:AAPL
.venv/bin/python -m stock_app.jobs run experiment:AAPL:binary
.venv/bin/python -m stock_app.jobs status
.venv/bin/python -m stock_app.jobs health
# Inspect status for the exact intended scheduled time before specifying a slot:
.venv/bin/python -m stock_app.jobs run refresh:AAPL --scheduled-at 2026-09-22T20:30:00Z
```

`run` defaults to the latest due slot, so invoking it twice is not a force-refresh.
Explicit timestamps must identify an actual due slot; equivalent timezones produce
the same identity. `run-due` runs refreshes before forecasts before experiments.
There is one worker across operational and legacy research execution.

`STOCK_RESEARCH_ROOT` selects the persistent directory. stdout is JSON, and run
lifecycle logs go to stderr as JSON. Logs include run identity, intended/start/end
times, attempt, pinned inputs, outputs and error class. Raw provider exception
text is deliberately excluded because it can contain credentials or URLs. Known
application validation failures have safe reasons. Do not put secrets in manual
reconciliation notes.

Exit codes: 0 successful/no work, 1 unresolved retry/failed/blocked runs (or a
health warning), 2 worker busy or invalid command. Run exit codes include earlier
unresolved runs; inspect JSON for detail. A successful download does not prove the
whole registry is healthy: invoke `health` as well.

## Persistent lifecycle and retries

Each run has a stable SHA-256 identity over registered job name and normalized
scheduled time. SQL uniqueness also enforces `(name, scheduled_at)`. The run stores
kind, symbol, task, actual timestamps, status, attempt number, idempotency key,
input state, produced identifiers and safe error information. Separate attempt
rows retain earlier retry failures; retries never erase that history.

The worker holds the existing OS lock for the whole finite invocation. A slow
worker cannot lose ownership to a time-based lease. Claiming a run and updating
its state use SQLite transactions. SQLite scheduling cursors and generated runs
commit together, so interruption cannot advance the cursor without run history.
The `running` record plus heartbeat makes interrupted work detectable on restart.

Preparation failures retry on later invocations after 60 and 120 seconds, for a
maximum of three attempts including the first. The typed job definition supports
bounded exponential backoff with a 900-second cap. No sleep or internal retry
loop is required. Cron granularity may delay a retry beyond its earliest due time.
Provider failure, invalid data, a lagging provider response, and missing/stale
forecast inputs fail **before** the effect fence. Existing valid snapshots remain
unchanged. Pre-validation failures do not create quarantine snapshots; their
run/attempt records retain the failure instead.

### Idempotency boundary and reconciliation

Underlying snapshot ingestion assigns random IDs and writes multiple files;
forecast/model services can partially commit. These operations are not an atomic
transaction with the operational database. This phase therefore makes an honest
at-most-once *attempt* guarantee after a durable `effects` fence, rather than
claiming exactly-once effects:

1. Acquire and validate input data; failures here can retry safely.
2. Persist the inputs and `effects` fence before any dataset/forecast/model write.
3. Call the existing service; record output IDs as soon as available.
4. If execution fails or the process dies after the fence, mark the run `blocked`.
   Never automatically repeat it, even if it may have died before the first write.
   Later slots of the same registered job are also blocked until reconciliation.

Successful and cancelled logical runs are never re-executed. Forecast inputs are
pinned before issuance, with no fallback download. Experiments use a dedicated
`operational-RUN_ID` legacy job and frozen input snapshots. Legacy automatic worker
recovery/execution excludes those experiment records so it cannot bypass the
fence. The original manual research CLI can still review/resume them explicitly.
No immutable dataset or research-history record is deleted or rewritten.

For blocked work, stop competing manual execution and inspect `status`, the
referenced legacy job, snapshot manifests/files, forecast IDs and experiment
checkpoints. A crash before metadata publication may leave an incomplete snapshot
directory; retain it for inspection. Use the existing manual experiment resume
command only after review if fitting must finish. Finish or cancel any adopted
legacy queued/running job before reconciliation. Then record what you verified:

```bash
.venv/bin/python -m stock_app.jobs reconcile RUN_ID \
  --outcome succeeded --note 'Verified output IDs in status and the referenced completed research job'
# Or retain partial evidence and abandon this slot without repeating it:
.venv/bin/python -m stock_app.jobs reconcile RUN_ID \
  --outcome cancelled --note 'Inspected partial output; retained evidence and abandoned this slot'
```

This command records a human decision, does not validate that decision or execute
work, and keeps the original blocked/failed attempt evidence. It cannot reset the
same slot for another attempt. Reconcile each affected blocked slot; future slots
can then execute. The runner never invokes reconciliation automatically.

## Downtime and migration

On first registration, the runner establishes a cursor at the latest eligible
slot. It cannot infer the intended operational history before registration; health
still checks existing data regardless of registration age. On subsequent starts,
every elapsed session/month is detected from the persisted cursor. Older missed
slots are recorded as `skipped`, and only the latest slot executes. Older queued
or safe-retry slots are explicitly superseded. Monthly misses follow the same
coalescing policy.

This policy avoids repeated full-history downloads and repeated latest-origin
forecasts. It does not invent historical provider vintages, retrospectively issue
prospective forecasts, or refit each missed month. Existing ledger rules still
classify late forecasts. Skipped slots and the retained input snapshots make the
boundary auditable. If an earlier run is already blocked, newer slots remain
blocked until reviewed.

Existing `job_keys` are consulted when a slot is registered. Completed legacy
scheduled identities are adopted without running again; any incomplete/failed
legacy identity is blocked for reconciliation rather than blindly retried.
Legacy enqueue historically merged some different keys into one active job, so
an adopted legacy identity is not proof of freshness: check source health.

Persistence is additive in `research.sqlite3`: `operational_schedules`,
`operational_runs`, `operational_attempts`, `operational_heartbeat`, and a run-status
index. The original research schema/version 1 and records remain compatible.
Reopening is idempotent; the existing SQLite backup includes all four tables.
Back up the **whole research directory** as well for external artifacts. Rollback
can stop external triggers and leave these ignored-by-old-code tables intact;
there is no destructive down migration. Do not run old recurring-scheduler code
at the same time as this runner.

## Freshness and health

`JobService.health()` is the application service for a future API/dashboard.
It derives freshness from checksum-verified persisted snapshot `created_at` and
`last_session`, never process uptime. Default policy:

| Status | Meaning |
|---|---|
| `fresh` | Latest required completed session, observation under 72 hours old, no newer failed/corrupt attempt |
| `warning` | One required session behind, observation at least 72 hours old, or fallback after a newer failed/corrupt attempt |
| `stale` | At least two required sessions behind or observation at least seven days old |
| `unknown` | Missing/unreadable/unverifiable metadata or snapshot, invalid/future timestamps |

The required session has a 30-minute post-close grace period. Age and session
thresholds are operational policies only; they do not change model features,
risk rules, prediction thresholds or research evaluation. Source health exposes
snapshot ID, observation time, last/expected sessions, lag and reason. Unknown
metadata never becomes an invented fresh timestamp. Forecasts/experiments require
all their inputs to be `fresh` before any effect.

Health also reports missing/overdue schedules, queued work, retries, failures,
blocked/interrupted runs, last persisted runner heartbeat and whether a worker
currently holds the lock. Heartbeats are written at invocation, run start,
effect boundary and completion. After 30 minutes without a heartbeat, health warns;
a held lock is reported separately, not interpreted as a dead worker. A long
experiment can therefore warn while still alive. Historical failed/blocked runs
remain warnings until manually reconciled; skipped runs remain audit history.

## Flask and external triggering

Flask no longer starts recurring scheduling at application startup or in its
manual-job worker loop. Existing dashboard/API manual submissions remain backward
compatible and can use the existing background worker. The legacy `schedule()`
method is retained for compatibility but is no longer automatically called.
`STOCK_RESEARCH_SCHEDULE` no longer controls recurring execution: enable/disable
the external trigger instead. Neither `status` nor `health` starts a worker.

For example, a local cron entry can trigger every five minutes (replace paths):

```cron
*/5 * * * * cd /absolute/path/just-looking-for-stocks && STOCK_RESEARCH_ROOT=/absolute/persistent/research /absolute/path/just-looking-for-stocks/.venv/bin/python -m stock_app.jobs run-due >> /absolute/path/job-runner.log 2>&1
```

Use an absolute interpreter and persistent root, configure log rotation, and run
`health` from your monitoring system so nonzero exits reach you. A launchd timer
or hosted scheduler can run the same command; the application logic is provider
independent. Keep the database and artifact directories together on persistent
local storage. An ephemeral hosted/GitHub Actions workspace without that storage
is not a durable deployment; do not share this flock/SQLite design across hosts.

A powered-off laptop cannot execute cron/launchd or deliver its own alerts.
On restart, the runner detects and coalesces missed work. If uninterrupted
collection or alerts during host downtime are needed, deploy the same CLI to an
always-on host and monitor it externally. No scheduler was installed or production
provider job executed as part of implementation.

## Validation and remaining scope

Offline tests cover successful recording, duplicate calls, process restart before
and after effects, bounded backoff, partial failure, same/separate-process lock
exclusion, missed sessions/months, holiday closes, source freshness/unknown data,
heartbeat, legacy migration, additive backup, CLI exit behavior, snapshot reuse,
pinned forecasts, candidate-only experiment registration, and reconciliation.
Run `.venv/bin/python -m pytest -q tests/test_operational_jobs.py` and the full suite.
There is no configured repository formatter/linter or type checker; existing
compilation, JavaScript contract checks and `git diff --check` remain applicable.

Limitations: one local worker; long fits can delay ingestion; snapshot/research
writes are guarded rather than transactional exactly-once operations; blocked
partial execution needs a human; health polling/notification delivery is external;
status/history growth and full missed-slot enumeration are not compacted in this
phase. Provider behavior and actual external trigger installation are not proven
by offline tests.

Phase 0.2 is implemented in the [canonical forecast ledger](canonical-forecast-ledger.md).
It retains immutable input revisions under one decision identity and freezes the
first accepted prediction for evaluation. Issuance kind is timing metadata, so a
late revision does not create another decision. The Phase 0 runner itself is unchanged.
