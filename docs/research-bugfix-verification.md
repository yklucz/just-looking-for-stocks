# Research regression testing — 2026-09-16

The testing pass started with 328 passing Python tests. It added **15 regression
cases**, each observed failing before its production fix. The final complete
suite passed **343 tests in 15.09 seconds**. This verifies software behavior;
it does not demonstrate better forecasting accuracy.

## Bugs reproduced and fixed

| Area | Observed failure before the fix | Corrected behavior and regression evidence |
| --- | --- | --- |
| Research request validation | Array-valued job/task fields and malformed universe settings raised server errors; a null symbol created a `NONE` job; object-shaped event records were accepted. | All five requests return 400 without saving jobs or events. `test_malformed_research_requests_return_400_without_writes` |
| Qualification lifecycle | Candidate, rejected, or retired models could be reported eligible using old nomination evidence. | Qualification requires a currently nominated shadow model. Three cases in `test_closed_or_unstarted_shadow_window_cannot_qualify`. |
| Interrupted training recovery | Registering a completed fit again changed an existing shadow model back into a candidate. | Existing model state and nomination are preserved within a transaction. `test_resumed_completed_fit_preserves_frozen_model_state` |
| Input freshness | A failed SPY refresh was hidden if the retained verified data was from the current session. | Prediction responses carry input freshness and flag the retained fallback as stale. `test_prediction_marks_current_session_fallback_inputs_stale` |
| Unavailable model artifacts | Missing model files returned a misleading 400; missing metadata caused an unhandled 500. | Both return JSON with 503 and `PREDICTION_UNAVAILABLE`, without issuing a forecast or changing the active binding. Two cases in `test_unavailable_active_artifact_returns_service_error`. |
| Event import integrity | A malformed later ID caused failure after earlier records had already been saved. A later database failure also left earlier updates committed. | IDs are validated before writes, and the entire batch uses one transaction. `test_invalid_event_id_rejects_entire_import` and `test_event_import_rolls_back_when_database_rejects_later_row`. |
| Optional event IDs | Mixing rows with and without an ID introduced a null database key and failed the import. | Omitted IDs receive generated identifiers; supplied IDs are preserved. `test_event_import_assigns_ids_to_rows_without_optional_id` |

Regression tests use temporary databases and artifacts. The transaction test
uses a real SQLite trigger to reject a later write and verifies that an earlier
update is rolled back. Prediction tests retain real artifact validation, data
fallback, ledger, and HTTP behavior; expensive estimator computation or external
data acquisition is substituted only where needed to isolate the failure.

## Verification performed

```sh
.venv/bin/python -m pytest -q
node scripts/test_chart_data.mjs
node scripts/test_frontend_contract.mjs
node scripts/test_research_frontend.mjs
.venv/bin/python -m compileall -q stock_app tests
git diff --check
```

All commands passed. The Python suite covers data validation and calendars,
causal features and availability, purged training and calibration, checkpoint
recovery, forecast resolution and revisions, model qualification and rollback,
jobs, imports, and API behavior. The Node checks cover chart semantics,
preferences, failed refreshes, training states, Research navigation, filters,
exports, and polling. Earlier offline-replay evidence remains documented in
[platform verification](research-verification.md).

The updated local server was restarted and checked in the browser:

- All four Research tabs selected and displayed their matching panels at
  **375, 768, and 1440 pixels**. All twelve checks had no horizontal overflow.
- Filtering by AAPL displayed AAPL records. Pending forecast outcomes and
  model qualification explanations remained visible.
- No browser console errors or warnings were captured during these checks.
- The market page loaded AAPL's quote, 390 intraday observations, and the
  existing model estimates. **Current → Price only**, the Line chart, and the
  unchecked estimate-range option were preserved.
- The viewport override was reset after testing.

At the final read-only database check, there were seven active legacy models,
eight shadow models, and two candidates. No activation was performed. Existing
research records, model files, and uncommitted chart work were preserved.

## Limits of this evidence

The deterministic suite and the browser check are separate from live-provider
reliability. The browser successfully loaded live market data, but this pass did
not run a comprehensive provider outage test against the external service or a
new live earnings/fundamentals download. Provider failure paths are tested with
fixtures. No new full training comparison was needed for these fixes; existing
small real-model training tests ran in the full suite. Prospective qualification
still requires sufficient future outcomes; passing software tests does not
establish forecasting skill.
