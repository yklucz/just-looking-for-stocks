# Initial local research results

Exported 2026-09-16T08:40:54.696748+00:00 from completed local jobs.

**These are historical research comparisons, not evidence of prospective forecasting skill.**

The first full run benchmarked AAPL before expanding to SPY, NVDA and TSLA. The table uses the latest completed run for each ticker/task; earlier AAPL runs remain preserved.

Evaluation origins span 2024-09-10 through 2026-09-04; resolved origins per comparison: 484. Exact fold boundaries and target dates appear in the JSON. Input download dates: 2026-09-15. They are immutable research inputs, not a claim about the latest available live price.

## Probability forecasts

Lower Brier score is better; the comparator predicts the fitting-window event frequency.

| Ticker | Model Brier | Training-prior Brier | ROC-AUC | Result |
|---|---:|---:|---:|---|
| AAPL | 0.259237 | 0.247784 | 0.5453 | Baseline not beaten |
| NVDA | 0.263102 | 0.249335 | 0.5207 | Baseline not beaten |
| SPY | 0.263069 | 0.245073 | 0.4719 | Baseline not beaten |
| TSLA | 0.252380 | 0.251502 | 0.5204 | Baseline not beaten |

## Return forecasts

MAE is in log-return units. The baseline predicts zero return (unchanged adjusted Close).

| Ticker | Model return MAE | Unchanged-price MAE | Price MAE (USD) | Nominal 80% range coverage |
|---|---:|---:|---:|---:|
| AAPL | 0.034597 | 0.031054 | 8.63 | 79.8% |
| NVDA | 0.046206 | 0.045071 | 7.37 | 88.8% |
| SPY | 0.015790 | 0.015378 | 9.88 | 86.0% |
| TSLA | 0.076462 | 0.059217 | 27.19 | 81.6% |

## Original versus market-context features

Each feature set selects its own training window/settings using validation only. Both are evaluated on matched outer dates. Lower is better; these are historical comparisons.

| Ticker | Task | Original 49 features | Context features |
|---|---|---:|---:|
| AAPL | binary brier | 0.259925 | 0.261367 |
| AAPL | regression mae | 0.034599 | 0.033892 |
| NVDA | binary brier | 0.262003 | 0.263172 |
| NVDA | regression mae | 0.044471 | 0.046206 |
| SPY | binary brier | 0.261198 | 0.263099 |
| SPY | regression mae | 0.015790 | 0.015703 |
| TSLA | binary brier | 0.255528 | 0.252867 |
| TSLA | regression mae | 0.075478 | 0.066374 |

## Interpretation

8 of 8 selected procedures did not beat their simple comparator on average primary error. Historical results alone do not qualify candidates for activation. Some individual folds can look favorable without establishing a consistent advantage. Nominal range coverage is a target; measured coverage varies by ticker and period.

At export: 7 legacy active models, 0 research active models and 8 frozen shadow candidates. Qualification requires at least 126 matched resolved prospective origins and positive paired confidence bounds before a user can activate a candidate.

Equal-weight cross-symbol results (no pooled dollar errors):

```json
{
  "binary": {
    "brier": 0.2594469953328371,
    "log_loss": 0.7124855257570744,
    "auc": 0.514579263438233,
    "balanced_accuracy": 0.5057636901125317
  },
  "regression": {
    "mae": 0.04326391264126264,
    "rmse": 0.05408771539584521,
    "interval_coverage": 0.8403925619834711
  }
}
```

## Reproducibility and evidence

The accompanying JSON includes job identifiers, checksums, exact input snapshot identifiers, fold boundaries, all baseline metrics and paired bootstrap confidence intervals. Full observations, fitting checkpoints and immutable model files remain under the ignored local research artifact directory. Preserve that directory with a SQLite backup.

Feature-comparison addenda, when present, evaluate each feature configuration on matched held-out origins. They preserve the original report and model bytes; inspecting these periods still makes them research comparisons.

```bash
python scripts/export_research_report.py
```

See [platform operations](research-platform.md) for backup, offline replay, recovery and rollback. Manual GRU support is tested separately; a full live-data GRU run was not included in these tabular results.
