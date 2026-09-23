# Local research platform

The Market notebook remains a simple Current-price chart. Open **Research** for
Data, Experiments, Models and Forecast history. These are research estimates;
completing a training job does not establish an accuracy advantage.

## Running and storage

Use Python **3.13.15** and the pinned requirements. Run
`python -m stock_app.app`, then open `http://127.0.0.1:49152/research`.
The supported local runtime is macOS/Linux. No broker, paid feed or cloud service
is required. Recurring execution uses an external trigger for the finite
[`stock_app.jobs` CLI](operational-jobs.md), independent of Flask.

`artifacts/research/research.sqlite3` stores versioned metadata, jobs, model
lifecycle and forecast records. Immutable market CSVs and JSON manifests live
in `artifacts/research/datasets`; models and experiment checkpoints live in
`artifacts/research/experiments`. Exact adjusted inputs used by legacy API
inference are separately pinned under `issued-inputs`; they do not pretend to
contain original unadjusted prices. All of these are Git-ignored local files.
`STOCK_RESEARCH_ROOT` selects an isolated research directory.

SQLite uses schema version 1, WAL and transactional updates. A filesystem lease
allows only one job worker across CLI/server processes. Do not delete snapshots
referenced by experiments or forecasts. Database backups do not contain external
artifact files: preserve the whole research directory for a complete backup.

```bash
python -m stock_app.research backup /absolute/new/path/research-backup.sqlite3
python -m stock_app.research export --kind forecasts --symbol AAPL > forecasts.json
python -m stock_app.research refresh --symbol AAPL
python -m stock_app.research experiment --symbol AAPL --task binary
python -m stock_app.research experiment --symbol AAPL --task regression
python -m stock_app.research experiment --symbol AAPL --include-gru
python -m stock_app.research resume JOB_ID
python -m stock_app.research replay JOB_ID --output artifacts/research/replays/my-run
python -m stock_app.research forecast --symbol AAPL
python -m stock_app.research company-context --symbol AAPL
```

Jobs pin snapshot identifiers before fitting. Resume reuses these inputs and
completed fits; changed input/configuration fingerprints require a new run.
The `replay` command loads only the recorded, checksum-verified snapshots and
configuration, with no provider request. Use a new output directory to refit;
the original artifact and active bindings stay preserved.
Training stops at fit boundaries after a two-hour budget; a fit already in
progress finishes before pausing or cancellation takes effect. The manual GRU
benchmark is binary-only, uses the existing fixed configuration on CPU, and is
excluded from monthly jobs. It cannot be promoted automatically.

## Data and features

Initial subjects are AAPL, SPY, NVDA and TSLA. The configurable expanded universe
adds MSFT, AMZN, GOOGL, META, JPM, XOM, JNJ and WMT. SPY/QQQ and sector ETFs are
downloaded as context. XLP is included for WMT rather than misclassifying Walmart
as consumer discretionary. This present-day list has selection/survivorship bias.

```bash
python -m stock_app.research universe AAPL SPY NVDA TSLA MSFT AMZN GOOGL META JPM XOM JNJ WMT
```

Yahoo `auto_adjust=False` downloads preserve source OHLCV, Adj Close, dividends
and splits. Yahoo already normalizes stock splits; this is not original tape
data. Adj Close / Close is explicitly applied to OHLC, with volume unchanged.
Prices are current-vintage adjusted data, not historical point-in-time corporate
action records. Raw and derived files have separate SHA-256 checksums.

Exchange calendars validate actual sessions and completion times, including
holidays and early closes. Invalid/missing candles are quarantined; no synthetic
rows shorten the target horizon. The last checksum-valid snapshot remains
available with stale status. Freshness is recomputed when reading current data.

The original 49 features remain available. The context configuration adds market
and sector returns/relative returns, beta, correlation, market volatility,
overnight gap, drawdown and ATR/Close. Inputs join on session and availability;
no backward filling, zero substitution or future matching is allowed. Both
feature configurations use matched complete origins for comparison.

Event/fundamental imports require `symbol`, `available_at`, and `event_date` or
`report_date`. `verified: true` also requires a source. Unverified values remain
viewable but excluded from training. These adapters do not manufacture a free
point-in-time earnings/fundamental archive. News sentiment and pooled multi-stock
models are outside this release.

The optional `company-context` job downloads Yahoo earnings and statements into
the events ledger for inspection through `/api/research/events`. These records
are explicitly unverified and excluded from historical training. Statement
period ends are never relabeled as publication dates. `import_point_in_time`
in `research/events.py` validates JSON/CSV archives with explicit timezone-aware
availability and retains the original import checksum.

## Evaluation and model lifecycle

Experiments retain five-session targets and the +0.002 log-return event. They
compare 756-session, 1,260-session and expanding training windows, two feature
sets and four fixed XGBoost settings. Each of four outer blocks spans 126 raw
sessions, with preceding 126-session validation and calibration blocks. Labels
crossing each boundary are purged; each full outer block yields 121 resolved
origins. Validation chooses configurations; separate calibration fits sigmoid
probabilities. Outer outcomes never select their own fold's parameters.

Reports compare probability losses/ranking and return/price errors with simple
baselines. Regression includes a nominal 80% quantile range and measured
coverage. Paired 20-session moving-block bootstrap uses 1,000 resamples. Per-symbol
reports retain dollar errors; cross-symbol summaries average dimensionless
metrics equally and identify potentially different evaluation periods.

The final deployment candidate uses the latest eligible fitting/calibration
blocks. Outer results evaluate the selection procedure, not independent outcomes
of that final model. Inference must occur after its fitting cutoff. Model labels
distinguish base-model training dates from the complete calibration cutoff.

Existing manifest bindings are imported as legacy active versions without
invented prospective evidence. New `/api/train` requests create persistent
candidates rather than binding them immediately. The lifecycle is candidate →
shadow → active → retired, with rejected versions retained.

Nominate a candidate in Models to freeze it for shadow evaluation. Replacing the
nomination closes that evidence window. After 126 matched resolved prospective
origins, classification requires ROC-AUC > 0.5 and positive lower confidence
bounds for Brier improvement over the frozen active comparator and training
prior. Regression requires the same improvement test for return MAE over the
active comparator and unchanged price. With no existing active model, qualification
uses the simple baseline only. Manual activation remains required. Previously
active versions can be restored with Rollback; failed validation cannot alter
the active pointer. The original manifest and old model files remain unchanged.

Issuance now uses a [canonical decision identity](canonical-forecast-ledger.md)
independent of input snapshots. Input changes produce immutable child revisions;
the first accepted revision remains authoritative for evaluation. On-time issuance means
after the origin close and before the next session opens. Late/catch-up estimates
are historical replays, not retrospective additions to the prospective record.
Input revisions retain their lineage under the same issuance. Corrected outcomes retain their
prior values. Outcomes use origin and target Close from one consistent adjusted
snapshot, converted to the price basis recorded at issuance. Qualification uses
the first issued forecast per origin and requires matched realized returns.

## Scheduling and APIs

Run `python -m stock_app.jobs run-due` from an external scheduler for daily data
refresh/forecast jobs and monthly candidate experiments. Flask's worker handles
manual submissions only. Durable slots, attempt history, bounded retries,
conservative partial-effect recovery, and source health are documented in
[operational jobs](operational-jobs.md). Missed slots are recorded and coalesced
to the latest eligible run; a powered-off computer still cannot execute work.
`STOCK_RESEARCH_SCHEDULE` is superseded by enabling/disabling the external trigger.
Use `python -m stock_app.jobs health` for freshness and runner warnings.

Existing prediction/history endpoints are preserved. New endpoints:

- `GET /api/research/datasets|jobs|models|forecasts|events`
- `GET /api/research/forecasts/<id>/revisions` — immutable input revision history
- `GET /api/research/forecast-ledger/audit` — read-only cardinality/integrity checks
- `GET /api/research/summary` — latest per-symbol and equal-weight results
- List filters: `symbol`, `model_id`, `kind`, `from`, `to`; `format=csv` exports
- `POST /api/research/jobs` — `kind`, `symbol`, `task`, optional `include_gru`
- `POST /api/research/jobs/<id>/cancel|resume`
- `POST /api/research/models/<id>/nominate|activate|rollback`
- `POST /api/research/settings` — configured `symbols` array
- `POST /api/research/events` — `kind` and `records` array

Mutations retain same-origin browser protection. Keep the service bound to
localhost; it is not a multi-user authenticated deployment. Prediction responses
include issuance provenance when recorded. A recording failure is explicitly
reported and must not count toward a prospective track record.

## Verification

```bash
python -m pytest -q
python -m compileall -q stock_app tests
node scripts/test_chart_data.mjs
node scripts/test_frontend_contract.mjs
node scripts/test_research_frontend.mjs
git diff --check
```

Tests isolate their SQLite databases and artifact directories. Offline fixtures
cover calendar boundaries, adjustments, missing/corrupt inputs, causal context,
purged evaluation, resume/integrity, actual estimator inference, prospective
qualification, rollback, and API/UI behavior. Live-provider checks and initial
research outcomes are recorded separately in `research-initial-results.md`.


## Immutable research experiment registry (Phase 1)

Candidate, replay, and scheduled experiment execution now passes through an immutable preregistration registry. Questions, falsifiable hypotheses, declared metric/spec contracts, parameter families, logical runs, numbered attempts, all terminal outcomes, and artifact provenance are retained in additive SQLite tables. Existing model computations, candidate lifecycle, and canonical forecast authority are unchanged.

Use `python -m stock_app.research registry history` for all outcomes and `python -m stock_app.research registry audit` for read-only integrity checks. Existing candidate commands remain compatible. Ten historical experiment jobs were imported as explicitly non-preregistered legacy evidence, with ten verified candidate links and no original-record, active-binding, or artifact changes. See [the complete registry contract](research-experiment-registry.md) for typed registration/execution, API/CLI examples, replay, reconciliation, migration evidence, and limitations. Phase 1.5 adds [evidence review, verification-only reproduction, and explicit operator reconciliation](research-integrity.md). The research agent remains unimplemented.


## Point-in-time foundation (Phase 2A)

The additive [PIT data foundation](point-in-time-data.md) provides permanent internal
source/entity/security/listing identities, immutable raw evidence and revisions,
explicit cutoff/availability policies, offline SEC/ALFRED contracts and leakage
checks. Use `python -m stock_app.research data audit` to inspect it. Existing Yahoo
workflows and model bindings are unchanged; no live provider backfill, training
consumer or historical PIT conversion has been introduced.
