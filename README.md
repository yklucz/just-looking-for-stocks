# Just Looking for Stocks

A Flask and Chart.js dashboard for researching stock-return predictions with Yahoo Finance data. The project combines causal market features, chronological validation, XGBoost and Logistic Regression baselines, a PyTorch GRU challenger, and a realistic paper-trading backtester.

This is a research application. It does not place trades, connect to a broker, or claim that its models have reliable predictive or economic value.

## Quick start

The currently verified environment is Python 3.13.15 on Apple Silicon. Dependencies are pinned in requirements.txt. From the repository root:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest -q -rs
python -m stock_app.app
```

Open [http://127.0.0.1:49152](http://127.0.0.1:49152). The `PORT` environment
variable can override this default:

```bash
PORT=49153 python -m stock_app.app
```

`requirements-dev.txt` includes the application requirements. TensorFlow and Keras are not required or installed.

## What the application does

The dashboard provides:

- Yahoo Finance ticker search and historical OHLCV charts
- causal technical features for research
- a saved XGBoost return-event predictor with automatic background training for new stocks
- a LONG/FLAT research estimate based on model probability
- a selectable chart overlay for the observed price, event probability, and a separate return-regression estimate
- 30-second refresh for visible dashboard tabs, plus a manual refresh control
- Line/Candlestick chart modes, adjustable future space, and zoom controls
- locally saved theme, ticker, range, interval, overlays, chart type, zoom span and refresh preferences
- historical backtest reports from frozen out-of-fold predictions

Selecting a stock reuses its saved active model. A missing model starts a persistent candidate-training job; completing training does not activate it. Open **Research** to inspect datasets and experiments, nominate a frozen shadow candidate, and review eligibility before manual activation. The price chart remains usable while training runs.

The local research platform adds immutable snapshots, SQLite records, calendar-aware data validation, market-context features, purged walk-forward comparisons, probability calibration, quantile ranges, a prospective forecast ledger, and reversible model activation. Daily refresh and monthly candidate jobs run while the server is open. Existing model files and manifest bindings are preserved.

See [research operations and interfaces](docs/research-platform.md) and [initial measured results](docs/research-initial-results.md) for commands, evidence boundaries, scheduling, backup, and recovery.

## Prediction definition

The preferred application model is XGBoost. The primary research target is:

```text
log(Close[t+5] / Close[t]) > 0.002
```

The API probability means:

```text
P(future five-candle log return is greater than +0.2%)
```

It is not the probability that price will fall, and it is not an exact future price forecast. The dashboard maps the probability to a simple research state:

```text
probability >= 0.5  -> LONG
otherwise           -> FLAT
```

Legacy probabilities are uncalibrated; research models report their calibration status. A LONG/FLAT state is a research estimate and does not execute an order.

## API

### `GET /api/predict`

```text
/api/predict?ticker=AAPL&model=xgboost
```

The response uses `schema_version: prediction-v2` and includes:

- `prediction.probability_up`
- `prediction.horizon`
- `prediction.event_threshold`
- `prediction.target_definition`
- `signal.action`
- `model.type`, `model.version`, and freshness fields
- completed-candle and market-data freshness information

Prediction is daily and uses completed Close candles regardless of the chart interval selected in the dashboard.

The chart’s `Both estimates` view uses the observed price axis for the historical line and a separate 0–100% axis for event probability. The dashed price connector appears only after a separate XGBoost return-regression model is trained; it joins the latest observed Close to one five-session estimated price and does not invent intermediate prices. Yahoo Finance may publish delayed candles, so refresh frequency cannot guarantee exchange real-time data.

The chart reserves 5, 10 or 20 blank future slots on the right. Daily slots count sessions; intraday slots count minutes. These are relative display slots, not exchange-calendar dates or extra predictions. On intraday charts the daily forecast endpoint is explicitly labelled as a separate horizon checkpoint and its spacing is schematic. Changing right space does not move this checkpoint. Weekly/monthly/five-day charts omit the connector because their candles do not count daily sessions; use a daily or intraday interval to see it. Historical daily probabilities appear only on the daily chart. Its connector starts at the completed Close used by the model, even if a newer live candle exists; that estimate is never silently rebased to a live quote.

`Max zoom` focuses on the latest two observed candles plus future space. Wheel/pinch and the `+`/`−` controls can zoom further; `Fit all` shows the full loaded range, and `Latest` resumes following new candles at your current zoom. When viewing older candles, refresh preserves the inspected timestamps. Refresh updates the existing Chart.js instance and table cells without clearing the chart or replaying its animation. A changed current candle replaces the existing point; a new timestamp adds a point. A failed refresh keeps the last display with an update warning. Quote/history polling continues during background model training.

Candlesticks draw actual Open/High/Low/Close bodies and wicks, with OHLC tooltips. Missing/invalid candle data is omitted; no future OHLC candles are fabricated. Display preferences are stored in this browser's local storage; clearing site data resets them. The saved theme is applied before the page paints, and unavailable local storage does not prevent the application from running.

The default view is `Current` with `Price only`: one price line, no redundant legend or empty forecast slots. Forecast/probability overlays remain selectable; explanatory notes are under `Chart details`. Its line ends at the latest available regular-market quote in the last display slot, with earlier points drawn from historical Close samples. The legend reads Current price and the endpoint tooltip includes the supplied quote timestamp. A horizontal marker identifies the same current value. If a quote is missing, the marker explicitly falls back to the latest candle Close. Quotes do not overwrite OHLC or rebase a model forecast. Existing saved display selections are retained.

The `Prediction accuracy` disclosure reports retrospective scores for resolved post-validation origins: classifier accuracy, ROC-AUC and Brier score, plus regression MAE, RMSE and MAPE. It includes dates, sample counts and simple baselines. The latest unresolved five-session targets are excluded. Classification and regression can have different evaluation windows because they have separate artifacts. The API limits these diagnostics to the most recent 2,000 eligible origins per model. These are historical replays of a frozen model, not a contemporaneously logged live forecast record.

A reproducible offline audit can use an existing frozen `market_history.csv` without downloading data or training:

```bash
python -m stock_app.prediction.audit --ticker AAPL --history /absolute/path/to/market_history.csv
```

The CLI evaluates all eligible origins in the supplied snapshot, checks the bound model contract, and emits JSON with model versions, source path, source checksum, dates and scores. See [the detailed prediction review](docs/prediction-review.md) and its linked JSON evidence.

### Other endpoints

```text
GET /api/info?ticker=AAPL
GET /api/history?ticker=AAPL&range=1m&interval=auto
GET /api/search?q=apple
GET /api/symbols
POST /api/train (JSON body: {"ticker":"NVDA", "task":"binary"|"regression"})
GET /api/prediction-chart?ticker=AAPL
```

Common prediction errors are returned with explicit codes:

- `MODEL_NOT_AVAILABLE`: no bound compatible artifact exists
- `MODEL_INCOMPATIBLE`: artifact metadata, feature order, fingerprint, or checksum fails
- `PREDICTION_UNAVAILABLE`: market data or inference cannot be completed
- `UNSUPPORTED_TASK`: the request asks for a prediction type the application does not provide

## Model and data architecture

```text
Yahoo Finance OHLCV
        ↓
timestamp and market-data validation
        ↓
causal feature engineering
        ↓
future-return target generation
        ↓
chronological split or walk-forward folds
        ↓
train-only preprocessing
        ↓
baseline, Logistic Regression, XGBoost, or GRU
        ↓
out-of-sample probabilities
        ↓
LONG/FLAT strategy and backtest
```

Feature engineering and scaling are separate. The same feature pipeline is used by the tabular models and the sequence model.

## Causal features

The default daily OHLCV configuration produces 49 ordered features, including:

- one- and multi-period returns and log returns
- SMA, EMA, and relative trend features
- RSI, ROC, MACD, and MACD signal features
- rolling volatility, true range, and ATR
- normalized candle structure
- relative volume and volume z-score
- cyclical day-of-week and month features

Indicators use only information available at or before their timestamp. The builder does not use centered windows, backfilling, future shifts, target values, or fitted scalers. SMA200 creates the default 199-row warm-up loss. Missing OHLCV fields cause dependent features to be omitted or validation to fail; values are never fabricated.

The public feature entry point is:

```python
from stock_app.config import FeatureConfig
from stock_app.features import build_features

result = build_features(history, FeatureConfig(interval="1d"))
model_frame = result.frame
feature_names = result.feature_names
metadata = result.metadata
```

## Research models

The repository contains these models:

- majority-class baseline
- training-prior probability baseline
- previous-return momentum baseline
- scaled Logistic Regression
- unscaled XGBoost classifier
- PyTorch GRU sequence challenger

The GRU is intentionally not promoted automatically. It must be compared with the simpler baselines using the same temporal folds and matched timestamps. There is no ensemble model.

## Training commands

Research experiments remain explicit offline actions. Dashboard training only creates a missing application XGBoost model. The primary binary research experiment uses `--horizon 5` and `--target-threshold 0.002`.

Train the baseline models:

```bash
python -m stock_app.training.train_baselines \
  --ticker AAPL \
  --interval 1d \
  --horizon 5 \
  --target-threshold 0.002
```

Run expanding walk-forward evaluation:

```bash
python -m stock_app.training.walk_forward \
  --ticker AAPL \
  --interval 1d \
  --horizon 5 \
  --target-threshold 0.002 \
  --mode expanding \
  --models majority,training_prior,momentum,logistic,xgboost
```

Run rolling evaluation:

```bash
python -m stock_app.training.walk_forward \
  --ticker AAPL \
  --interval 1d \
  --horizon 5 \
  --target-threshold 0.002 \
  --mode rolling \
  --models majority,training_prior,momentum,logistic,xgboost
```

Train the GRU challenger separately:

```bash
python -m stock_app.training.train_gru \
  --ticker AAPL \
  --interval 1d \
  --horizon 5 \
  --target-threshold 0.002
```

Every walk-forward fold creates a fresh model and train-only preprocessor. Validation controls model selection and early stopping; the test fold is used only for final fold evaluation. Out-of-fold predictions are saved for later backtesting.

## Model artifacts and application inference

Artifacts are written under `artifacts/`, which is intentionally ignored by Git. The application manifest is `config/prediction_models.json` and binds a ticker and model name to a saved artifact plus its feature fingerprint and checksum.

After an offline training run, bind a trusted XGBoost artifact:

```bash
python -m stock_app.prediction.bind \
  --ticker AAPL \
  --artifact /absolute/path/to/xgboost
```

The application verifies the artifact before inference. It checks the model type, ticker, interval, target definition, horizon, threshold, feature order, configuration fingerprint, and model checksum. Do not manually edit a binding to bypass those checks.

`GET /api/predict` remains inference-only and returns `MODEL_NOT_AVAILABLE` until a compatible model exists. The dashboard handles that response by calling `POST /api/train` with JSON `{"ticker":"NVDA"}`, polling its status, and requesting prediction again when ready. Offline artifact binding remains available for explicitly trained models.

## Backtesting

Stage 5 consumes frozen Stage 4 out-of-fold predictions. It does not retrain models. The default strategy is a non-overlapping LONG/FLAT policy:

- signal generated after candle `t` closes
- entry at `Open[t+1]`
- five-candle holding period
- no overlapping full-size positions
- no short selling for the binary target
- 1 bp commission and 1 bp slippage per side by default
- unlevered position size capped at 100%

Run a backtest from a frozen walk-forward artifact:

```bash
python -m stock_app.backtest.run \
  --artifact /absolute/path/to/stage4-run \
  --model xgboost \
  --threshold 0.5 \
  --holding-period 5 \
  --commission-bps 1 \
  --slippage-bps 1
```

The engine reports net and gross performance, equity, drawdown, CAGR, volatility, Sharpe, Sortino, Calmar, win rate, profit factor, expectancy, trade count, exposure, turnover, and total costs. Buy & Hold and cash benchmarks use the same evaluation period as the strategy.

Historical backtest results are not guarantees of future performance.

## Verification

Run the full project checks from an activated `.venv`:

```bash
python -m pytest -q -rs
python -m compileall -q stock_app tests scripts
git diff --check
node scripts/test_frontend_contract.mjs
node scripts/test_chart_data.mjs
node scripts/test_research_frontend.mjs
```

The Python suite has 684 passing tests in the latest local verification. The frontend checks cover saved preferences, chart switching, in-place refresh, failed refreshes, candidate training, Research tabs and exports. Chart checks cover future spacing, forecast anchors, Current-price behavior, optional ranges, viewport continuity and OHLC drawing. See [research verification](docs/research-verification.md) for the separate offline, live-provider and responsive-browser evidence, [regression testing](docs/research-bugfix-verification.md) for the failure-path checks, and the phase-specific verification documents for the operational, forecast-ledger, registry, integrity, and point-in-time additions.

## Project layout

```text
stock_app/
├── app.py                 Flask routes and application entry point
├── stock_service.py       Yahoo search, history, and caching
├── features/              Causal OHLCV feature builders
├── targets/               Future-return target generation
├── models/                Baselines, Logistic, XGBoost, and GRU
├── training/              Splits, datasets, training, and walk-forward runs
├── backtest/              Frozen-OOF execution and performance metrics
├── prediction/            Validated artifact loading and API inference
├── research/              SQLite records, verified data, experiments and model lifecycle
└── stocks_dashboard/      HTML, JavaScript, and CSS frontend

config/                    Explicit application model bindings
artifacts/                 Local research outputs; ignored by Git
docs/                      Historical stage reports and verification evidence
scripts/                   Research reports and frontend contract checks
tests/                     Unit, leakage, model, API, and backtest tests
```

## Limitations and research boundaries

- Yahoo Finance availability and data quality can vary.
- Daily candles are conservatively restricted to completed observations.
- Legacy model probabilities remain uncalibrated. Research candidates use separate-block sigmoid calibration; this does not guarantee accurate probabilities.
- Overlapping five-candle targets are statistically dependent.
- A single ticker or walk-forward run is not evidence of a durable trading edge.
- Feature importance does not establish causality.
- The project is for research and paper trading only; it has no brokerage execution.

Historical implementation reports are kept in [`docs/`](docs/) so the README can describe the current system without presenting old experiments as current setup requirements.
