# Stage 3 AAPL research observation

Recorded 2026-09-10 from Yahoo Finance through the existing daily-history cache. This is one single-split experiment, not evidence of trading profitability.

Command: `python -m stock_app.training.train_baselines --ticker AAPL --horizon 5`

Raw candles: 3692; feature-valid rows: 3493; aligned rows: 3488; features: 49; warm-up loss: 199.

Target: future 5-candle log return > 0.002. Decision probability cutoff: 0.5. No rebalancing. Fixed defaults; no test-based tuning.

| Partition | Samples | Positive | Negative | Positive ratio | Origin start | Origin end | Last target |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| train | 2380 | 1323 | 1057 | 0.5559 | 2012-10-16 | 2022-03-31 | 2022-04-07 |
| validation | 549 | 282 | 267 | 0.5137 | 2022-04-08 | 2024-06-14 | 2024-06-24 |
| test | 549 | 291 | 258 | 0.5301 | 2024-06-25 | 2026-09-01 | 2026-09-09 |

## Train metrics

| Model | Accuracy | Balanced accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC (AP) | Brier | Log loss | Directional accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| majority | 0.5559 | 0.5000 | 0.5559 | 1.0000 | 0.7146 | 0.5000 | 0.5559 | 0.4441 | 16.0076 | 0.5794 |
| training_prior | 0.5559 | 0.5000 | 0.5559 | 1.0000 | 0.7146 | 0.5000 | 0.5559 | 0.2469 | 0.6869 | 0.5794 |
| momentum | 0.5155 | 0.5129 | 0.5680 | 0.5367 | 0.5519 | 0.5129 | 0.5624 | 0.4845 | 17.4615 | 0.5189 |
| logistic | 0.5882 | 0.5586 | 0.5934 | 0.8239 | 0.6899 | 0.6031 | 0.6471 | 0.2383 | 0.6693 | 0.6017 |
| xgboost | 0.5559 | 0.5000 | 0.5559 | 1.0000 | 0.7146 | 0.6297 | 0.6481 | 0.2440 | 0.6812 | 0.5794 |

## Validation metrics

| Model | Accuracy | Balanced accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC (AP) | Brier | Log loss | Directional accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| majority | 0.5137 | 0.5000 | 0.5137 | 1.0000 | 0.6787 | 0.5000 | 0.5137 | 0.4863 | 17.5294 | 0.5464 |
| training_prior | 0.5137 | 0.5000 | 0.5137 | 1.0000 | 0.6787 | 0.5000 | 0.5137 | 0.2516 | 0.6964 | 0.5464 |
| momentum | 0.5064 | 0.5059 | 0.5193 | 0.5248 | 0.5220 | 0.5059 | 0.5166 | 0.4936 | 17.7920 | 0.5137 |
| logistic | 0.5464 | 0.5361 | 0.5342 | 0.9149 | 0.6745 | 0.5614 | 0.5521 | 0.2543 | 0.7035 | 0.5756 |
| xgboost | 0.5137 | 0.5000 | 0.5137 | 1.0000 | 0.6787 | 0.5328 | 0.5290 | 0.2500 | 0.6932 | 0.5464 |

## Test metrics

| Model | Accuracy | Balanced accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC (AP) | Brier | Log loss | Directional accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| majority | 0.5301 | 0.5000 | 0.5301 | 1.0000 | 0.6929 | 0.5000 | 0.5301 | 0.4699 | 16.9385 | 0.5556 |
| training_prior | 0.5301 | 0.5000 | 0.5301 | 1.0000 | 0.6929 | 0.5000 | 0.5301 | 0.2498 | 0.6927 | 0.5556 |
| momentum | 0.5100 | 0.5070 | 0.5364 | 0.5567 | 0.5464 | 0.5070 | 0.5336 | 0.4900 | 17.6607 | 0.5209 |
| logistic | 0.4954 | 0.4867 | 0.5198 | 0.6323 | 0.5705 | 0.4935 | 0.5553 | 0.2689 | 0.7364 | 0.5137 |
| xgboost | 0.5301 | 0.5000 | 0.5301 | 1.0000 | 0.6929 | 0.5633 | 0.5719 | 0.2481 | 0.6893 | 0.5556 |

## Interpretation

XGBoost slightly improved Brier and log loss versus the training-prior baseline on this test sample, with ROC-AUC 0.5633. However, all predictions were UP at the fixed 0.5 cutoff, so accuracy/F1 matched the majority baseline. Logistic validation discrimination did not persist on test. No settings were changed after observing this test set.

Hard majority/momentum outputs are 0/1 scores, so their large log losses reflect wrong predictions with degenerate certainty. The training-prior baseline is the more useful probability-quality reference.

PR-AUC means average precision. Directional accuracy uses future return > 0, distinct from the +0.002 target. Conditional future returns in report.json are diagnostics, not executed trading returns. No calibration, walk-forward folds, costs or significance estimates are provided.

XGBoost selected best_iteration=2 (zero-based; three trees) by validation log loss, from a maximum of 200 rounds. Training time: logistic 0.0184s; XGBoost 0.0510s.

## Top XGBoost gain features

| Feature | Mean gain (best-iteration trees) |
| --- | ---: |
| volume_mean_5 | 22.0642 |
| macd_histogram | 21.0434 |
| atr_14 | 18.1243 |
| volume_mean_20 | 17.5668 |
| ema_10_ema_50_ratio | 15.5869 |
| ema_20 | 14.6967 |
| lower_wick | 14.0346 |
| macd | 13.2253 |
| sma_5 | 12.0048 |
| ema_50 | 11.8288 |

Gain describes fitted splits, not causality or stable predictive value.

## Provenance and artifacts

Local full report: `artifacts/AAPL/1d/h5/20260910T074039_95b1f127/report.json`. Each model subdirectory contains its model, metadata and feature order. These generated artifacts are gitignored.

```json
{
  "versions": {
    "numpy": "2.4.6",
    "pandas": "3.0.5",
    "scikit-learn": "1.9.0",
    "xgboost": "3.2.0"
  },
  "git_revision": "12577868d67ec1411bf680a0a39b72c919b9e779",
  "git_dirty": true,
  "created_at": "2026-09-10T07:40:39.731080+00:00",
  "source": "Yahoo Finance via existing daily history cache",
  "raw_data_sha256": "60563f0dc3e9917e92b52036bffb25590aa622752ad415406493a07995228f1f"
}
```

Historical adjusted data may be revised by Yahoo. Full raw-data hashes are retained, but no source-data snapshot was saved in this run. Git dirty status records that these stages are still uncommitted.
