# Research platform verification — 2026-09-16

This record separates software verification from forecast performance.

## Deterministic checks

The complete Python suite passed **328 tests**, covering existing dashboard behavior and the new research
platform. Three frontend Node checks passed for chart semantics, dashboard contracts,
and the Research view. Python compilation and whitespace checks also pass.

Research coverage includes exchange holidays/early closes and timezones; raw and
adjusted prices/corporate actions; missing/duplicate/incomplete candles; provider
failure and checksum fallback; availability-aware context joins; purged temporal
splits, validation-only selection and separate calibration; actual XGBoost and
small GRU fits; cancellation, resumption and checkpoint integrity; matched feature
comparisons; forecast idempotency/revisions and late replay labels; unresolved,
missing and recovered outcomes; paired prospective qualification; transactional
activation failure and rollback; worker exclusion, restart deduplication and
monthly training that preserves frozen shadow nominations.

Tests use isolated temporary databases and artifacts. Synthetic passing
qualification tests demonstrate the gates, not real forecasting skill.

## Offline reproduction

A full AAPL binary experiment was rerun with network access restricted using the
recorded input snapshots and configuration:

- Source job: `c700b0f6f53948d0bd5f15e7ac6cf6f1`
- Replay directory: `artifacts/research/replays/initial-aapl-binary`
- Completed successfully; aggregate metrics and input/configuration fingerprint
  exactly match the original report.
- Original candidate, source report and model bindings were preserved.

Per-feature comparison addenda for all eight initial ticker/task comparisons
also completed against pinned snapshots. Their source report hashes bind them to
the original evidence; the original report/model bytes were preserved.

## Live-provider and server checks

Live Yahoo daily snapshots were collected for AAPL, SPY, NVDA, TSLA and configured
market/sector ETFs. The September 16 refresh reached completed September 15
sessions for all four initial subjects. Daily forecast jobs completed with no
per-model errors: AAPL 4 forecasts, SPY 3, NVDA 4, TSLA 4, covering active and
shadow models. SPY has no legacy active regressor. These outcomes are pending.

Seven original legacy models remain active; eight new frozen candidates are
shadow models. No candidate was activated. The original binding manifest is
unchanged. Provider failures remain visible and are separate from offline test
results. The free earnings/fundamentals adapter was verified with deterministic
provider fixtures; a live earnings/fundamentals download was not part of this run.

## Browser checks

The actual local Research page loaded and its Data, Experiments, Models and
Forecast history tabs were checked at 375, 768 and 1440 pixels. All twelve layout
checks had no horizontal overflow or visible request errors. No console errors
or warnings were captured during these Research checks. Date filtering worked
against the real AAPL experiment records. Ineligible models showed no Activate
button, and full training/calibration cutoffs were visible.

The market page loaded live AAPL history and its current quote. Current → Price
only remained selected; the Line chart showed Current mode. Candlesticks retain
their explicit OHLC labeling. Forecast ranges remain opt-in. Long-running,
missing-model, failed-refresh and retained-data UI states also have deterministic
frontend coverage; they are not simulated prospective evidence.

The local server runs at `http://127.0.0.1:49152/`, with Research at `/research`.
Scheduling operates only while that server is running.

See [initial results](research-initial-results.md) for measured model/baseline
errors and [operations](research-platform.md) for backup, replay and recovery.
