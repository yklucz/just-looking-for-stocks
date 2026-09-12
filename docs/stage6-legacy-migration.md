# Legacy sequence-model migration boundary

Stage 6 adds an independently evaluated PyTorch **binary classifier**. It cannot
produce the legacy price-model outputs through a valid mathematical conversion.

## Current dependency chain

- `GET /api/predict` in `stock_app/app.py` resolves the ticker, selected range,
  interval and price field, then calls `stock_app.stock_service.predict_prices`.
- `predict_prices` selects a price-only series, calls
  `stock_app.training.legacy_model.load_or_train_model`, computes held-out price
  predictions/RMSE and recursively generates a price forecast path.
- `legacy_model` lazily imports Keras for training/loading. It uses versioned
  `stage1_<contract hash>.keras`, scaler `.pkl` and metadata `.json` files under
  the legacy model directory. Old unverified artifacts are ignored.
- `/api/history`, `/api/search`, `/api/info`, `/api/symbols`, and the static
  dashboard do not require TensorFlow for imports or ordinary data access.

## Response and frontend contract

The existing dashboard's `loadPrediction` path in
`stock_app/stocks_dashboard/script.js` consumes:

- `predicted_next_price` and the close-specific `predicted_next_close` alias;
- `predicted_next_date`, `predicted_close_price`, `next_point`, `forecast_path`;
- `rmse` and historical `validation` rows containing `Actual` and `Prediction`;
- interval/price-field labels and the existing price-chart/table elements.

The new GRU estimates P(five-candle log return > .002) from 49 daily features.
It has no expected-return regression head. That event probability does not identify
an expected price, closing price, forecast path or price RMSE. Its 64-feature-row
history and daily Close-return event also cannot silently replace the legacy
price-field/interval-specific task.

## Migration decision for Stage 6

**TensorFlow/Keras remains temporarily.** The technical blocker is the incompatible
prediction/response semantics of the current price dashboard, not a reason to
restore a next-price neural model or fabricate prices from probabilities.

The research GRU trains and performs inference through the shared research model
interface. Normal Flask prediction requests are not redirected to research training.
No legacy adapter, imports, dependencies or user artifacts are deleted. Existing
legacy correctness tests remain; the real Keras integration test continues to skip
when Keras is not installed in the execution environment. This is not a claim that
the legacy prediction endpoint works without its required backend.

A later explicit probability-oriented API/UI migration can retain nullable
deprecated price fields, replace the primary card/chart labels with clearly labeled
probability estimates, require an offline saved-model binding per ticker/interval,
and show an unavailable/stale status without training on requests. A separately
validated return regressor could instead support a secondary estimated-price
field. Neither implementation should be inferred from binary probabilities.

Only after that replacement/compatibility path is tested end to end should
TensorFlow/Keras be removed and the legacy integration tests retired or replaced.
Keeping the current adapter during Stage 6 preserves the existing routes while
avoiding a false claim of a completed TensorFlow replacement.
