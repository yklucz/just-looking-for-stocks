# Stage 6.5: TensorFlow removal

## Dependency and contract audit

Audited first-party source, dependency files, scripts, tests, documentation and the
root model directory for tensorflow, keras, tf., .keras, load_model, Sequential,
LSTM, EarlyStopping and tensorflow-metal. Environment packages were excluded from
source decisions. Historical research reports were inspected but preserved.

The only runtime chain was:

`stocks_dashboard/script.js loadPrediction → GET /api/predict → stock_service.predict_prices → training.legacy_model → Keras`

The adapter trained on cache misses, loaded/saved `.keras` weights, and used a
single-price scaler. TensorFlow appeared in `requirements.txt`; the separate
`requirements-metal.txt` contained only two obsolete backend packages. No scripts
needed that file after removing its README setup instructions. Remaining
`nn.Sequential` and `load_model` uses belong to PyTorch/XGBoost/shared registry.

| Old field / component | Decision | Replacement |
| --- | --- | --- |
| predicted_next_price / predicted_next_close | REMOVE | prediction.probability_up |
| predicted_next_date | REPLACE | timestamp identifies the last completed input candle |
| predicted_close_price / next_point | REMOVE | No binary-to-price conversion exists |
| forecast_path / future chart overlay | REMOVE | Daily probability/target/signal card |
| rmse | REMOVE | No application price-regression metric |
| validation / actual-vs-predicted chart/table | REMOVE | Offline research reports remain available |
| ticker / symbol | KEEP | Both identify the resolved ticker |
| interval / chart controls | KEEP, separate responsibilities | Prediction is 1d; controls still govern history |
| history / search / info / symbols | KEEP | Existing Yahoo integrations |

## Modern inference

`stock_app/prediction/` contains service, error schema, explicit artifact loader
and offline binding CLI. The default and currently supported application model is
XGBoost. GRU remains an independent research challenger.

The response is `prediction-v2`: binary five-candle log-return event probability
with threshold .002, LONG/FLAT at .5, target definition, model type/fingerprint,
training/validation dates, model/data freshness, and research limitations.
Probabilities are uncalibrated. No exact prices, path or RMSE are fabricated.

Only saved weights are loaded. No API request trains, selects, calibrates or
rescales a model. The binding checks model type/ticker/interval/target, feature
configuration/order, contract fingerprint and weight checksum on every request,
including cache hits. Repeated validated loads use a bounded four-entry cache.
History uses the existing Yahoo cache with explicit auto-adjusted OHLC retrieval.

The default bindings reference AAPL/SPY final expanding-fold XGBoost artifacts.
Their training labels end 2023-12-04 and validation labels end 2024-12-02. They are
explicitly stale research candidates; selection was by training chronology, not
model performance. Artifacts are not committed. A fresh clone reports
`MODEL_NOT_AVAILABLE` until an operator trains offline and binds an artifact.

Feature calculations retain the artifact's full starting history, rather than
truncating recursive indicators to a recent window. Today's market-local daily
candle is always excluded, including after close, before OHLC validation. This
conservative policy prevents incomplete daily candles entering inference. Invalid
completed historical OHLC is rejected, never filled, rescaled or silently replaced
with a frozen snapshot. Invalid/missing artifacts and data failures have explicit
JSON error codes and HTTP 503 responses. Unsupported prediction tasks return 400.

## Removed and retained

Removed the legacy adapter, recursive price forecasts, legacy training config,
legacy backend tests, two TensorFlow/Keras requirements, and the Metal-only
requirements file. General Stage 1 alignment/preprocessing/price-target utilities
remain for non-neural regression tests. No research models/results/artifacts were
deleted or retrained. Earlier reports describe their historical stage accurately;
they are not current installation instructions.

The root `models/` directory contained zero files at audit time. The ignore rule
remains; any legacy files supplied later will not be loaded. `.venv311` was
preserved. `.venv*/` now ignores all virtual environments. README recommends only
`.venv`; `.venv-clean` is the isolated verification environment.

## Clean environment evidence

Created with Python 3.11, with no system-site packages. Installed declared
requirements and development requirements. A PyPI wheel transfer failed, so the
already downloaded official PyTorch wheel was supplied to pip as a local wheel;
all other dependencies were installed normally from the requirements. No packages
were copied from `.venv311`. Core artifact-dependent versions are pinned:
PyTorch 2.14.0, XGBoost 3.2.0, scikit-learn 1.9.0. `pip check` found no conflicts.

`find_spec('tensorflow')` and `find_spec('keras')` both return None. The clean
suite passed 226 tests with zero skips, including actual CPU GRU training and
save/reload. All 36 original GRU checkpoints were independently loaded in the
clean environment and reproduced their OOF probabilities exactly; all baseline
columns and Stage 4 source manifests remained unchanged. Compilation, whitespace
checks, core imports and Flask startup passed.

Hardware: arm64 Python, macOS 26.6.2, Apple M4, MPS-capable PyTorch build. Inside
the tool sandbox MPS availability is false. Outside it, MPS availability is true
and an actual MPS tensor sum succeeded. CUDA is unavailable. CPU remains valid.
No packages were reinstalled to change accelerator detection.

## Application and frontend verification

Live local Flask HTTP checks returned 200 for `/`, `/api/search`, `/api/info`,
`/api/history`, and AAPL `/api/predict`. AAPL used the completed 2026-09-09 candle
and returned probability 0.4836540520, FLAT, and stale-model status. These are
inference observations, not new research performance results. MSFT has no bound
artifact and returned 503 `MODEL_NOT_AVAILABLE`.

SPY live prediction returned 503 `PREDICTION_UNAVAILABLE` because Yahoo supplied
completed history violating OHLC relationships. This remains a provider-data
limitation; validation was not weakened. Recorded prediction responses are in
`docs/stage65-live-api.json`.

`node scripts/test_frontend_contract.mjs` executes the actual dashboard module
with test-only DOM and Chart adapters. It checks probability display, stale status,
search options, historical chart values, independent price/interval controls,
missing-model errors, and rejection of invalid probabilities. `node --check`
also passed. This is runtime contract testing, not a visual browser check.
Computer-use verification had no connected browser/app and a native-pipe startup
failure, so visual rendering, zoom/pan and responsive appearance were not manually
verified. Historical Chart.js rendering code and dashboard CSS remain intact.

No ensemble or tuning was performed. Research conclusions from Stages 1–6 remain
unchanged; removing a runtime dependency does not establish model economic value.
