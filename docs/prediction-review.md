**Prediction and chart review — 13–14 September 2026**

The chart defects identified in this review are fixed. The saved prediction models have not been retrained, replaced, or promoted. The measured results do not establish a dependable forecasting edge.

**What was wrong, and what changed**

| Finding | Previous behavior | Result |
| --- | --- | --- |
| “Current” did not use the quote | Both Current and Close selected historical Close; the default was Close. | Current is the new default. The displayed line ends at the latest quote, with a Current price legend, Latest quote endpoint and timestamped tooltip. Historical OHLC remains intact. A horizontal quote marker is included in the price scale. Missing quotes explicitly fall back to the latest candle Close. |
| Forecast-only data distorted the observed line | An absent forecast origin was inserted into the observed price dataset. | The anchor stays separate from observed values and OHLC bodies. It is labelled completed Close and is excluded from the visible candle count. Max zoom includes two actual observations even when a separate anchor is present. |
| Daily origin could match the wrong candle | Date-only matching attached a daily origin to the last matching minute or aggregated candle. | Intraday views receive an explicit separate daily Close anchor. Weekly, monthly, and five-day views omit the connector and explain which intervals support it. |
| Blank space changed the forecast position | On intraday charts, the endpoint offset was `futureBars`. | Blank space no longer determines the forecast endpoint. Its daily horizon checkpoint remains schematic and explicitly labelled. |
| Daily probabilities could appear on earlier candles | Daily probabilities were assigned to the last matching intraday timestamp or an aggregate's opening date. | Historical probabilities are displayed only on daily charts, avoiding this misleading time alignment. The current probability remains visible in the model panel. |
| Regression errors could erase valid probabilities | A regression data/output error escaped the chart endpoint. | Such errors return an unavailable forecast while retaining the valid classification result. |
| Accuracy was not visible | Automatically trained dashboard artifacts had empty metrics, and the UI showed a model probability without evaluation statistics. | The API and an expandable UI panel now evaluate resolved post-validation predictions against explicit baselines. An offline audit command records reproducible JSON evidence. |

Current price does not mean exchange real-time. The marker uses the latest available regular-market quote, with its supplied timestamp. It replaces only the last displayed line value; the underlying candles, history table, Close mode, and forecast origin stay unchanged. Earlier line samples remain historical Closes because the API does not supply historical quote ticks. A browser's previously saved explicit price choice is retained; select **Current** once if an older saved Close selection is still active. The normal in-app dashboard inspected in this session was explicitly switched to Line → Current.

Implementation entry points: [chart geometry](../stock_app/stocks_dashboard/chart-data.js), [quote service](../stock_app/stock_service.py), [dashboard rendering](../stock_app/stocks_dashboard/script.js), [preferences](../stock_app/stocks_dashboard/preferences.js), [forecast service](../stock_app/prediction/chart.py), and [accuracy calculations](../stock_app/prediction/evaluation.py).

**What the prediction actually means**

The classifier estimates the event `log(Close[t+5] / Close[t]) > 0.002`. This is a return above a +0.2% log threshold over five daily candles. A displayed probability of 63% is a model output; it is not a claim of 63% historical accuracy. A probability below 50% is also not necessarily a prediction of falling prices: a small positive return can miss the threshold.

The price connector comes from a separate regressor. It predicts a log return `r`, then displays `origin_close * exp(r)` as one horizon endpoint. It does not estimate the intermediate path. Exponentiating a predicted log return also does not generally give the arithmetic expected price without accounting for the return distribution. The UI therefore calls it an estimated Close, not a guaranteed target or expected-profit calculation. No calibrated probability, confidence interval, or path uncertainty is supplied.

**Fresh audit of the currently bound models**

These results were recomputed during this review using the checked local model bindings and frozen Yahoo OHLCV snapshots ending **9 September 2026**. Each model is scored only on origins strictly after its own validation label end and only when the five-session outcome is present. The last five unresolved origins are excluded. Models were not fitted or selected during this audit.

| Bound classifier | Resolved origins | Origin period | Event accuracy | Always-event baseline | Balanced accuracy | ROC-AUC | Brier |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |
| AAPL XGBoost | 437 | 2024-12-03 to 2026-09-01 | 51.95% | 51.95% | 50.68% | 0.4990 | 0.2497 |
| SPY XGBoost | 437 | 2024-12-03 to 2026-09-01 | 56.06% | 56.06% | 50.00% | 0.5000 | 0.2463 |

AAPL matched the simple baseline on classification accuracy, with essentially chance ranking. SPY predicted the event for every resolved origin; its 56.06% accuracy is the event frequency, not useful discrimination. ROC-AUC 0.5 represents chance ranking. Brier score measures squared probability error; lower is better, but it must be interpreted alongside a relevant baseline and event frequency.

| AAPL price regressor | Model | Unchanged-Close baseline |
| --- | ---: | ---: |
| Mean absolute price error (MAE) | $7.5697 | $7.6053 |
| Root mean squared price error (RMSE) | $9.8982 | $9.9681 |
| Mean absolute percentage error (MAPE) | 3.0925% | 3.1161% |
| Directional accuracy | 49.45% | Not used as the baseline comparison |

The regression audit contains **548 resolved origins**, from **2024-06-26 through 2026-09-01**. Its MAE improvement over unchanged Close is approximately **$0.036 per forecast, or 0.47%**. This is a small descriptive difference, not demonstrated statistical significance or profitability. Its period differs from the classifier because the artifacts have different fitting dates. No compatible SPY price regressor is currently bound, so no SPY regression score was invented.

Exact model fingerprints, fitting dates, history paths/checksums and unrounded results are saved in [AAPL evidence](prediction-audit-aapl.json) and [SPY evidence](prediction-audit-spy.json). NVDA and TSLA have saved model bindings, but this review did not have equivalent frozen full-history snapshots for them; their accuracy remains unverified here.

**Final normal-server check after Yahoo recovered**

On 14 September, the restarted API successfully downloaded daily history through 11 September and returned the new evaluation fields. This includes two additional resolved origins compared with the pinned offline snapshots above:

| AAPL normal-server diagnostic | Model | Baseline | Resolved origins |
| --- | ---: | ---: | ---: |
| Return-event accuracy | 51.9362% | 52.1640% always-event | 439 |
| ROC-AUC | 0.4963 | 0.5 chance ranking | 439 |
| Price MAE | $7.5536 | $7.5880 unchanged Close | 550 |
| Price RMSE | $9.8822 | $9.9517 unchanged Close | 550 |
| Price MAPE | 3.0847% | 3.1079% unchanged Close | 550 |

Origins now end on 3 September; their outcomes extend through 11 September. The observed quote was $332.27, timestamped `2026-09-11T20:00:01+00:00`. The five-session regression endpoint was $331.96 from the completed 11 September Close. These are dated observations and estimates, not claims about a later current price or an already realized forecast outcome.

The [captured normal-server response summary](prediction-audit-live-aapl.json) records this check. Its input history is not pinned to a stored checksum, unlike the reproducible offline reports. The conclusion is unchanged: the classifier did not beat the baseline; the regression advantage is very small. The normal browser showed the accuracy panel, quote timestamp, Daily Close anchor label and two actual candles under Max zoom, with no inspected console errors or warnings.

**How this relates to the existing research**

The repository's separate [Stage 6 report](stage6-research.md) records four historical expanding/rolling walk-forward comparisons. Its XGBoost event accuracies are 52.71% and 53.38% for AAPL, versus a 56.44% majority baseline; for SPY they are 53.02% and 53.96%, versus a 57.60% majority baseline. Those are previously recorded research results, not the fresh bound-model replay above. Their periods and model populations differ, so they should not be combined into one “accuracy.” They provide no strong contrary evidence to the weak discrimination observed in this review.

**Code safeguards reviewed**

The target builder counts future raw candles before feature alignment, and removes unresolved targets. The chronological splitter purges training/validation origins whose labels would cross partition boundaries. XGBoost fits on TRAIN with VALIDATION for early stopping. The saved artifact loader checks fingerprints, checksums, ticker, feature order, interval, and target configuration. Inference excludes the current daily candle conservatively and requires its origin to follow validation outcomes.

The new evaluator reuses those target semantics. It neither selects thresholds nor fits a calibrator. Tests use hand-calculated outcomes to verify sample eligibility, unresolved counts, classification scores, regression errors and baseline comparisons. An empty evaluation is reported as insufficient data, not zero error or zero accuracy.

These checks support correctness of the reviewed calculation and temporal boundaries. They do not constitute an exhaustive security audit or establish that every historical research choice was independent of observed test performance.

**Important limits of the evidence**

- These are retrospective predictions from frozen model weights, not forecasts logged when each historical origin occurred. In particular, dashboard models can have artifact creation dates later than the replayed dates. A real prospective track record remains necessary.
- Consecutive five-session targets overlap; sample counts are not counts of independent experiments. No independence-based significance claim or confidence interval is made.
- Current downloaded adjusted prices can differ from an earlier snapshot after corporate actions or data corrections. The offline reports pin the exact history checksum; live dashboard statistics can change with the supplied data.
- Classification and regression errors do not include commissions, slippage, execution delays, or a trading strategy. The small regression error improvement is not an economic return.
- Automatically trained models retain the original 70/15/15 fitting design, which can leave the model's effective training information old. This review did not silently retrain them on the evaluation period.
- During this session, the existing live server returned quotes and intraday prices, but its long daily-history fetch returned `PREDICTION_UNAVAILABLE: No historical data available for this ticker.` A later refresh recovered the full-history request and returned a daily forecast on the normal intraday dashboard. Both this live frontend check and an isolated, visibly labelled frozen-data preview were used; no frozen data was substituted into the normal server. The normal Flask server was subsequently restarted and both the new evaluation API and quote timestamp were verified in the normal dashboard.

**Verification and reproduction**

The initial suite passed 259 Python tests. The completed changes passed **265 Python tests**, the frontend contract check, the chart-data check, Python compilation, and `git diff --check`. One older source-text test banned the substring `rmse` as part of legacy removal; that overly broad ban was removed because the new independent regressor legitimately reports price RMSE. The other legacy-contract checks remain.

Browser verification covered the Current marker, daily forecast connector, maximum zoom, Line/Candlestick switching, expanded accuracy details, and page widths of 375, 768 and 1440 pixels. No document-level horizontal overflow was measured at those widths, and the inspected preview had no console warnings or errors. Intraday anchor separation and endpoint stability were tested with deterministic data cases. A later normal-server check also showed the separate daily Close anchor on the live intraday chart. This verifies display/inference availability, not the correctness of a future outcome.

From an activated environment:

```bash
python -m pytest -q -rs
python -m compileall -q stock_app tests scripts
node scripts/test_frontend_contract.mjs
node scripts/test_chart_data.mjs
git diff --check

python -m stock_app.prediction.audit --ticker AAPL --history artifacts/AAPL/1d/h5/walk_forward/20260910T081655_ca5887a2/market_history.csv
python -m stock_app.prediction.audit --ticker SPY --history artifacts/SPY/1d/h5/walk_forward/20260910T081700_fdf71a33/market_history.csv
```

The API diagnostics use the latest 2,000 eligible origins per model; the offline command evaluates all eligible origins in the supplied snapshot. No dependency installation or model-binding change is required. The local Flask server was restarted during this review and the page was reloaded; the updated API is active at http://127.0.0.1:49152. Future Python edits still require a server restart because debug reload is disabled.

The connector behavior follows Chart.js's documented handling of gaps in [line datasets](https://www.chartjs.org/docs/latest/charts/line.html). The temporal evaluation approach is consistent with scikit-learn's guidance to preserve time ordering in [time-series cross-validation](https://scikit-learn.org/stable/modules/cross_validation.html#time-series-split).
