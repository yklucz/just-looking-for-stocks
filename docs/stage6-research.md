# Stage 6 frozen GRU challenger research

This is a fixed-configuration research comparison, not evidence of future profitability. No parameters or thresholds were selected from these outcomes.

Primary protocol: 49 daily features, sequence length 64, binary `log(Close[t+5]/Close[t]) > 0.002`, probability cutoff 0.5. Nine original folds per run; no warm start. Each test set contains 2,250 matched origins. Baseline OOF probabilities are copied from checksum-validated Stage 4 artifacts, not retrained.

Economics: existing Stage 5 LONG/FLAT engine; enter next Open, exit five bars later; one non-overlapping position, no same-open re-entry; 1 bp commission and 1 bp slippage per side. Buy & Hold uses identical dates and costs. Overlapping five-day labels are dependent observations.

Machine-readable experiment: `artifacts/stage6_comparison_3eafe812.json`

## AAPL expanding

OOF: 2250 matched rows; 3692 frozen candles. Evaluation: 2016-12-21 to 2025-12-10.

| Model | OOF ROC-AUC | PR-AUC (AP) | Brier | Log loss | Accuracy | Balanced accuracy | F1 | Net Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.5644 | 0.4356 | 15.6990 | 0.5644 | 0.5000 | 0.7216 | — |
| training_prior | 0.4626 | 0.5434 | 0.2482 | 0.6895 | 0.5644 | 0.5000 | 0.7216 | 1.0893 |
| momentum | 0.5080 | 0.5684 | 0.4871 | 17.5573 | 0.5129 | 0.5080 | 0.5584 | 0.6650 |
| logistic | 0.4960 | 0.5526 | 0.3721 | 1.1146 | 0.4507 | 0.4857 | 0.3056 | 0.1237 |
| xgboost | 0.4863 | 0.5561 | 0.2492 | 0.6915 | 0.5271 | 0.4982 | 0.6331 | 0.9785 |
| gru | 0.4707 | 0.5509 | 0.2582 | 0.7101 | 0.4578 | 0.4724 | 0.4278 | 0.2936 |
| Buy & Hold | — | — | — | — | — | — | — | 1.0239 |

| Model | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar | Profit factor | Trades | Exposure | Costs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| training_prior | 905.76% | 29.35% | 1.0893 | 1.6202 | -30.78% | 0.9535 | 1.4035 | 375 | 83.19% | $70,990.72 |
| momentum | 232.11% | 14.32% | 0.6650 | 0.9548 | -36.38% | 0.3936 | 1.2580 | 329 | 72.98% | $27,995.21 |
| logistic | 7.06% | 0.76% | 0.1237 | 0.1741 | -23.54% | 0.0324 | 1.0421 | 113 | 25.07% | $4,812.15 |
| xgboost | 580.27% | 23.83% | 0.9785 | 1.4619 | -29.56% | 0.8063 | 1.4567 | 288 | 63.89% | $40,970.70 |
| gru | 39.39% | 3.77% | 0.2936 | 0.4182 | -26.01% | 0.1450 | 1.1153 | 166 | 36.82% | $9,463.76 |
| Buy & Hold | 931.32% | 29.71% | 1.0239 | 1.4964 | -37.39% | 0.7946 | — | 1 | 100.00% | $226.29 |

GRU ROC-AUC beats XGBoost in **5/9** matched folds and exceeds 0.50 in **5/9**. Training prior is a probability baseline; Buy & Hold has no ML metrics.

| GRU fold metric | Mean | Median | Std (population) | Min | Max |
| --- | --- | --- | --- | --- | --- |
| roc_auc | 0.4944 | 0.5073 | 0.0607 | 0.4077 | 0.5908 |
| pr_auc | 0.5633 | 0.5805 | 0.0752 | 0.4581 | 0.6802 |
| brier_score | 0.2582 | 0.2548 | 0.0124 | 0.2437 | 0.2903 |
| log_loss | 0.7101 | 0.7028 | 0.0263 | 0.6805 | 0.7790 |
| f1 | 0.3639 | 0.4268 | 0.2190 | 0.0741 | 0.7277 |

| Fold | Test start | Test end | GRU AUC | XGB AUC | GRU AP | Brier | Log loss | F1 | Best/final epoch | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2016-12-20 | 2017-12-15 | 0.5908 | 0.5443 | 0.6802 | 0.2630 | 0.7193 | 0.0809 | 1/6 | 1.0244 |
| 1 | 2017-12-18 | 2018-12-14 | 0.5500 | 0.5099 | 0.5445 | 0.2516 | 0.6964 | 0.0741 | 1/6 | 1.3280 |
| 2 | 2018-12-17 | 2019-12-12 | 0.4682 | 0.5221 | 0.6505 | 0.2903 | 0.7790 | 0.4268 | 4/9 | 2.5385 |
| 3 | 2019-12-13 | 2020-12-09 | 0.4077 | 0.4475 | 0.5805 | 0.2580 | 0.7091 | 0.1443 | 1/6 | 1.9855 |
| 4 | 2020-12-10 | 2021-12-07 | 0.5523 | 0.5406 | 0.6166 | 0.2437 | 0.6805 | 0.7277 | 1/6 | 2.3807 |
| 5 | 2021-12-08 | 2022-12-05 | 0.5142 | 0.5020 | 0.4581 | 0.2540 | 0.7012 | 0.4833 | 3/8 | 3.4624 |
| 6 | 2022-12-06 | 2023-12-04 | 0.5073 | 0.4439 | 0.5828 | 0.2558 | 0.7048 | 0.5448 | 3/8 | 4.0403 |
| 7 | 2023-12-05 | 2024-12-02 | 0.4418 | 0.4939 | 0.4893 | 0.2524 | 0.6979 | 0.2667 | 1/6 | 3.1893 |
| 8 | 2024-12-03 | 2025-12-02 | 0.4177 | 0.5450 | 0.4673 | 0.2548 | 0.7028 | 0.5267 | 1/6 | 3.4709 |

| Min | Q25 | Median | Q75 | Max | Mean | Std | Fraction >= 0.5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.2354 | 0.4555 | 0.4887 | 0.5300 | 0.6963 | 0.4929 | 0.0642 | 0.3831 |

Training total: 23.42 seconds. Device(s): ['cpu']. Trainable parameters: 57473. Final validation loss > train loss + 0.15 in 8/9 folds. Final validation loss > best loss + 0.02 in 9/9 folds.

Test inference per fold (250 sequences): [18.78, 18.79, 18.38, 18.74, 18.91, 18.61, 18.94, 18.77, 18.91] ms. These timings include batched network inference and device transfers; feature construction/scaling are excluded.

Probability buckets are descriptive calibration/outcome diagnostics, not calibrated predictions or trading returns.

| lower | upper | upper_inclusive | observations | mean_probability | event_frequency | mean_future_log_return | mean_future_simple_return |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.4 | False | 134 | 0.34007 | 0.63433 | 0.0089 | 0.0095 |
| 0.4 | 0.45 | False | 277 | 0.43808 | 0.51625 | 0.00347 | 0.00395 |
| 0.45 | 0.5 | False | 977 | 0.47609 | 0.5998 | 0.00813 | 0.00902 |
| 0.5 | 0.55 | False | 423 | 0.52044 | 0.50118 | -0.00143 | -0.00046 |
| 0.55 | 0.6 | False | 350 | 0.572 | 0.54857 | 0.00386 | 0.00443 |
| 0.6 | 1.0 | True | 89 | 0.63513 | 0.58427 | 0.00967 | 0.01062 |

Walk-forward artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/AAPL/1d/h5/walk_forward/20260910T152115_d30db4a5`

Backtest artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/AAPL/1d/h5/walk_forward/20260910T152115_d30db4a5/backtest/20260910T152141_0e2f336d`

## AAPL rolling

OOF: 2250 matched rows; 3692 frozen candles. Evaluation: 2016-12-21 to 2025-12-10.

| Model | OOF ROC-AUC | PR-AUC (AP) | Brier | Log loss | Accuracy | Balanced accuracy | F1 | Net Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.5644 | 0.4356 | 15.6990 | 0.5644 | 0.5000 | 0.7216 | — |
| training_prior | 0.4512 | 0.5283 | 0.2497 | 0.6926 | 0.5644 | 0.5000 | 0.7216 | 1.0893 |
| momentum | 0.5080 | 0.5684 | 0.4871 | 17.5573 | 0.5129 | 0.5080 | 0.5584 | 0.6650 |
| logistic | 0.4951 | 0.5521 | 0.3830 | 1.1987 | 0.4658 | 0.4967 | 0.3517 | 0.3937 |
| xgboost | 0.5151 | 0.5769 | 0.2480 | 0.6892 | 0.5338 | 0.5053 | 0.6374 | 0.6777 |
| gru | 0.5038 | 0.5712 | 0.2600 | 0.7137 | 0.4573 | 0.4791 | 0.3922 | 0.3237 |
| Buy & Hold | — | — | — | — | — | — | — | 1.0239 |

| Model | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar | Profit factor | Trades | Exposure | Costs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| training_prior | 905.76% | 29.35% | 1.0893 | 1.6202 | -30.78% | 0.9535 | 1.4035 | 375 | 83.19% | $70,990.72 |
| momentum | 232.11% | 14.32% | 0.6650 | 0.9548 | -36.38% | 0.3936 | 1.2580 | 329 | 72.98% | $27,995.21 |
| logistic | 57.99% | 5.23% | 0.3937 | 0.5798 | -28.01% | 0.1868 | 1.4102 | 125 | 27.73% | $5,096.28 |
| xgboost | 244.74% | 14.80% | 0.6777 | 0.9846 | -37.37% | 0.3959 | 1.2920 | 290 | 64.33% | $24,993.03 |
| gru | 43.98% | 4.15% | 0.3237 | 0.4434 | -39.00% | 0.1063 | 1.1550 | 137 | 30.39% | $7,596.42 |
| Buy & Hold | 931.32% | 29.71% | 1.0239 | 1.4964 | -37.39% | 0.7946 | — | 1 | 100.00% | $226.29 |

GRU ROC-AUC beats XGBoost in **6/9** matched folds and exceeds 0.50 in **4/9**. Training prior is a probability baseline; Buy & Hold has no ML metrics.

| GRU fold metric | Mean | Median | Std (population) | Min | Max |
| --- | --- | --- | --- | --- | --- |
| roc_auc | 0.5102 | 0.4998 | 0.0640 | 0.4203 | 0.6200 |
| pr_auc | 0.5698 | 0.5622 | 0.0845 | 0.4228 | 0.6802 |
| brier_score | 0.2600 | 0.2609 | 0.0096 | 0.2452 | 0.2733 |
| log_loss | 0.7137 | 0.7151 | 0.0198 | 0.6836 | 0.7414 |
| f1 | 0.2857 | 0.2667 | 0.2618 | 0.0000 | 0.7277 |

| Fold | Test start | Test end | GRU AUC | XGB AUC | GRU AP | Brier | Log loss | F1 | Best/final epoch | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2016-12-20 | 2017-12-15 | 0.5908 | 0.5443 | 0.6802 | 0.2630 | 0.7193 | 0.0809 | 1/6 | 1.0429 |
| 1 | 2017-12-18 | 2018-12-14 | 0.5516 | 0.4737 | 0.5422 | 0.2519 | 0.6970 | 0.0730 | 1/6 | 1.3255 |
| 2 | 2018-12-17 | 2019-12-12 | 0.4998 | 0.4939 | 0.6625 | 0.2476 | 0.6883 | 0.5704 | 2/7 | 1.5654 |
| 3 | 2019-12-13 | 2020-12-09 | 0.4297 | 0.5853 | 0.5919 | 0.2591 | 0.7115 | 0.2667 | 2/7 | 1.5662 |
| 4 | 2020-12-10 | 2021-12-07 | 0.6200 | 0.5340 | 0.6704 | 0.2452 | 0.6836 | 0.7277 | 3/8 | 1.9308 |
| 5 | 2021-12-08 | 2022-12-05 | 0.4777 | 0.4817 | 0.4228 | 0.2609 | 0.7151 | 0.5845 | 1/6 | 1.4368 |
| 6 | 2022-12-06 | 2023-12-04 | 0.5192 | 0.5050 | 0.5622 | 0.2661 | 0.7259 | 0.2680 | 5/10 | 2.3524 |
| 7 | 2023-12-05 | 2024-12-02 | 0.4203 | 0.5261 | 0.4886 | 0.2732 | 0.7412 | 0.0000 | 1/6 | 1.3600 |
| 8 | 2024-12-03 | 2025-12-02 | 0.4824 | 0.4631 | 0.5075 | 0.2733 | 0.7414 | 0.0000 | 1/6 | 1.3502 |

| Min | Q25 | Median | Q75 | Max | Mean | Std | Fraction >= 0.5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.3272 | 0.4163 | 0.4694 | 0.5179 | 0.6822 | 0.4775 | 0.0832 | 0.3284 |

Training total: 13.93 seconds. Device(s): ['cpu']. Trainable parameters: 57473. Final validation loss > train loss + 0.15 in 8/9 folds. Final validation loss > best loss + 0.02 in 9/9 folds.

Test inference per fold (250 sequences): [19.07, 18.91, 19.08, 18.76, 19.85, 19.31, 18.76, 19.0, 19.09] ms. These timings include batched network inference and device transfers; feature construction/scaling are excluded.

Probability buckets are descriptive calibration/outcome diagnostics, not calibrated predictions or trading returns.

| lower | upper | upper_inclusive | observations | mean_probability | event_frequency | mean_future_log_return | mean_future_simple_return |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.4 | False | 473 | 0.37094 | 0.53911 | 0.004 | 0.00488 |
| 0.4 | 0.45 | False | 413 | 0.43303 | 0.53269 | 0.00363 | 0.00403 |
| 0.45 | 0.5 | False | 625 | 0.47605 | 0.6416 | 0.00985 | 0.01075 |
| 0.5 | 0.55 | False | 350 | 0.52022 | 0.56 | 0.00369 | 0.0048 |
| 0.55 | 0.6 | False | 133 | 0.5722 | 0.40602 | -0.00371 | -0.00305 |
| 0.6 | 1.0 | True | 256 | 0.642 | 0.5625 | 0.00531 | 0.00593 |

Walk-forward artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/AAPL/1d/h5/walk_forward/20260910T152141_190ed093`

Backtest artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/AAPL/1d/h5/walk_forward/20260910T152141_190ed093/backtest/20260910T152157_95bee62a`

## SPY expanding

OOF: 2250 matched rows; 3692 frozen candles. Evaluation: 2016-12-21 to 2025-12-10.

| Model | OOF ROC-AUC | PR-AUC (AP) | Brier | Log loss | Accuracy | Balanced accuracy | F1 | Net Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.5760 | 0.4240 | 15.2825 | 0.5760 | 0.5000 | 0.7310 | — |
| training_prior | 0.4822 | 0.5609 | 0.2447 | 0.6825 | 0.5760 | 0.5000 | 0.7310 | 0.9124 |
| momentum | 0.4858 | 0.5692 | 0.5053 | 18.2141 | 0.4947 | 0.4858 | 0.5536 | 0.6635 |
| logistic | 0.4730 | 0.5551 | 0.3165 | 0.8848 | 0.4796 | 0.4919 | 0.4761 | 0.1028 |
| xgboost | 0.4685 | 0.5482 | 0.2581 | 0.7120 | 0.5302 | 0.4910 | 0.6475 | 0.6829 |
| gru | 0.4807 | 0.5561 | 0.2553 | 0.7042 | 0.5253 | 0.4927 | 0.6320 | 0.5224 |
| Buy & Hold | — | — | — | — | — | — | — | 0.8739 |

| Model | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar | Profit factor | Trades | Exposure | Costs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| training_prior | 232.62% | 14.34% | 0.9124 | 1.2867 | -29.65% | 0.4836 | 1.4683 | 375 | 83.19% | $28,727.26 |
| momentum | 117.68% | 9.06% | 0.6635 | 0.9149 | -30.40% | 0.2980 | 1.3465 | 330 | 73.20% | $19,678.61 |
| logistic | 4.66% | 0.51% | 0.1028 | 0.1343 | -38.59% | 0.0132 | 1.0299 | 204 | 45.25% | $7,864.24 |
| xgboost | 132.67% | 9.87% | 0.6829 | 0.9559 | -29.65% | 0.3329 | 1.3579 | 295 | 65.44% | $19,881.98 |
| gru | 85.00% | 7.10% | 0.5224 | 0.7265 | -26.81% | 0.2648 | 1.2985 | 274 | 60.78% | $14,994.65 |
| Buy & Hold | 247.30% | 14.89% | 0.8739 | 1.2098 | -32.05% | 0.4646 | — | 1 | 100.00% | $89.47 |

GRU ROC-AUC beats XGBoost in **6/9** matched folds and exceeds 0.50 in **4/9**. Training prior is a probability baseline; Buy & Hold has no ML metrics.

| GRU fold metric | Mean | Median | Std (population) | Min | Max |
| --- | --- | --- | --- | --- | --- |
| roc_auc | 0.5161 | 0.4906 | 0.0679 | 0.4350 | 0.6100 |
| pr_auc | 0.5972 | 0.5883 | 0.0834 | 0.4724 | 0.7290 |
| brier_score | 0.2553 | 0.2483 | 0.0173 | 0.2315 | 0.2805 |
| log_loss | 0.7042 | 0.6898 | 0.0352 | 0.6560 | 0.7551 |
| f1 | 0.5625 | 0.7185 | 0.2486 | 0.0000 | 0.7854 |

| Fold | Test start | Test end | GRU AUC | XGB AUC | GRU AP | Brier | Log loss | F1 | Best/final epoch | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2016-12-20 | 2017-12-15 | 0.6100 | 0.5583 | 0.6704 | 0.2805 | 0.7551 | 0.0000 | 1/6 | 1.0689 |
| 1 | 2017-12-18 | 2018-12-14 | 0.4581 | 0.4661 | 0.5091 | 0.2752 | 0.7452 | 0.4245 | 2/7 | 1.6022 |
| 2 | 2018-12-17 | 2019-12-12 | 0.4679 | 0.4368 | 0.6123 | 0.2315 | 0.6560 | 0.7854 | 3/8 | 2.1803 |
| 3 | 2019-12-13 | 2020-12-09 | 0.4906 | 0.4669 | 0.5883 | 0.2458 | 0.6852 | 0.7185 | 1/6 | 2.0025 |
| 4 | 2020-12-10 | 2021-12-07 | 0.5992 | 0.5607 | 0.6965 | 0.2384 | 0.6696 | 0.7437 | 2/7 | 2.7760 |
| 5 | 2021-12-08 | 2022-12-05 | 0.5501 | 0.4856 | 0.4724 | 0.2795 | 0.7537 | 0.6072 | 1/6 | 2.6056 |
| 6 | 2022-12-06 | 2023-12-04 | 0.4350 | 0.5173 | 0.5199 | 0.2477 | 0.6885 | 0.7268 | 3/8 | 3.8387 |
| 7 | 2023-12-05 | 2024-12-02 | 0.5932 | 0.5147 | 0.7290 | 0.2509 | 0.6950 | 0.3249 | 1/6 | 3.1310 |
| 8 | 2024-12-03 | 2025-12-02 | 0.4404 | 0.5000 | 0.5764 | 0.2483 | 0.6898 | 0.7311 | 1/6 | 3.5356 |

| Min | Q25 | Median | Q75 | Max | Mean | Std | Fraction >= 0.5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.3506 | 0.4893 | 0.5421 | 0.6080 | 0.6898 | 0.5364 | 0.0841 | 0.7138 |

Training total: 22.74 seconds. Device(s): ['cpu']. Trainable parameters: 57473. Final validation loss > train loss + 0.15 in 7/9 folds. Final validation loss > best loss + 0.02 in 9/9 folds.

Test inference per fold (250 sequences): [18.83, 18.91, 19.34, 19.14, 18.85, 19.05, 18.77, 18.75, 19.98] ms. These timings include batched network inference and device transfers; feature construction/scaling are excluded.

Probability buckets are descriptive calibration/outcome diagnostics, not calibrated predictions or trading returns.

| lower | upper | upper_inclusive | observations | mean_probability | event_frequency | mean_future_log_return | mean_future_simple_return |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.4 | False | 306 | 0.38403 | 0.55882 | 0.00159 | 0.00168 |
| 0.4 | 0.45 | False | 73 | 0.41579 | 0.69863 | 0.00595 | 0.00602 |
| 0.45 | 0.5 | False | 265 | 0.48348 | 0.59245 | 0.00404 | 0.00419 |
| 0.5 | 0.55 | False | 542 | 0.52239 | 0.60332 | 0.00319 | 0.00346 |
| 0.55 | 0.6 | False | 436 | 0.57572 | 0.56193 | 0.00256 | 0.00276 |
| 0.6 | 1.0 | True | 628 | 0.6318 | 0.54936 | 0.00225 | 0.00279 |

Walk-forward artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/SPY/1d/h5/walk_forward/20260910T152157_6ac456bf`

Backtest artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/SPY/1d/h5/walk_forward/20260910T152157_6ac456bf/backtest/20260910T152222_9a6d663a`

## SPY rolling

OOF: 2250 matched rows; 3692 frozen candles. Evaluation: 2016-12-21 to 2025-12-10.

| Model | OOF ROC-AUC | PR-AUC (AP) | Brier | Log loss | Accuracy | Balanced accuracy | F1 | Net Sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.5760 | 0.4240 | 15.2825 | 0.5760 | 0.5000 | 0.7310 | — |
| training_prior | 0.4614 | 0.5435 | 0.2458 | 0.6848 | 0.5760 | 0.5000 | 0.7310 | 0.9124 |
| momentum | 0.4858 | 0.5692 | 0.5053 | 18.2141 | 0.4947 | 0.4858 | 0.5536 | 0.6635 |
| logistic | 0.4910 | 0.5769 | 0.3560 | 1.0567 | 0.4644 | 0.4842 | 0.4324 | 0.3812 |
| xgboost | 0.4861 | 0.5710 | 0.2505 | 0.6944 | 0.5396 | 0.5000 | 0.6554 | 0.4725 |
| gru | 0.4942 | 0.5858 | 0.2615 | 0.7167 | 0.4920 | 0.4788 | 0.5619 | 0.5128 |
| Buy & Hold | — | — | — | — | — | — | — | 0.8739 |

| Model | Net return | CAGR | Sharpe | Sortino | Max DD | Calmar | Profit factor | Trades | Exposure | Costs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| training_prior | 232.62% | 14.34% | 0.9124 | 1.2867 | -29.65% | 0.4836 | 1.4683 | 375 | 83.19% | $28,727.26 |
| momentum | 117.68% | 9.06% | 0.6635 | 0.9149 | -30.40% | 0.2980 | 1.3465 | 330 | 73.20% | $19,678.61 |
| logistic | 40.34% | 3.85% | 0.3812 | 0.5234 | -24.94% | 0.1544 | 1.2423 | 166 | 36.82% | $8,344.86 |
| xgboost | 75.28% | 6.46% | 0.4725 | 0.6416 | -32.14% | 0.2009 | 1.2529 | 301 | 66.77% | $14,946.50 |
| gru | 72.21% | 6.25% | 0.5128 | 0.7105 | -26.81% | 0.2330 | 1.3118 | 222 | 49.25% | $11,581.48 |
| Buy & Hold | 247.30% | 14.89% | 0.8739 | 1.2098 | -32.05% | 0.4646 | — | 1 | 100.00% | $89.47 |

GRU ROC-AUC beats XGBoost in **4/9** matched folds and exceeds 0.50 in **5/9**. Training prior is a probability baseline; Buy & Hold has no ML metrics.

| GRU fold metric | Mean | Median | Std (population) | Min | Max |
| --- | --- | --- | --- | --- | --- |
| roc_auc | 0.5116 | 0.5048 | 0.0483 | 0.4581 | 0.6100 |
| pr_auc | 0.6025 | 0.6420 | 0.0879 | 0.4084 | 0.6886 |
| brier_score | 0.2615 | 0.2688 | 0.0205 | 0.2273 | 0.2917 |
| log_loss | 0.7167 | 0.7312 | 0.0419 | 0.6469 | 0.7786 |
| f1 | 0.4412 | 0.6072 | 0.3274 | 0.0000 | 0.7835 |

| Fold | Test start | Test end | GRU AUC | XGB AUC | GRU AP | Brier | Log loss | F1 | Best/final epoch | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 2016-12-20 | 2017-12-15 | 0.6100 | 0.5583 | 0.6704 | 0.2805 | 0.7551 | 0.0000 | 1/6 | 1.0386 |
| 1 | 2017-12-18 | 2018-12-14 | 0.4581 | 0.4575 | 0.5044 | 0.2743 | 0.7432 | 0.4180 | 2/7 | 1.5464 |
| 2 | 2018-12-17 | 2019-12-12 | 0.5678 | 0.4768 | 0.6886 | 0.2273 | 0.6469 | 0.7835 | 1/6 | 1.3481 |
| 3 | 2019-12-13 | 2020-12-09 | 0.4733 | 0.4740 | 0.5730 | 0.2474 | 0.6882 | 0.6969 | 1/6 | 1.3417 |
| 4 | 2020-12-10 | 2021-12-07 | 0.5061 | 0.5230 | 0.6420 | 0.2414 | 0.6758 | 0.7437 | 1/6 | 1.3370 |
| 5 | 2021-12-08 | 2022-12-05 | 0.4581 | 0.5671 | 0.4084 | 0.2688 | 0.7312 | 0.6072 | 1/6 | 1.3762 |
| 6 | 2022-12-06 | 2023-12-04 | 0.5345 | 0.5559 | 0.6529 | 0.2447 | 0.6825 | 0.7212 | 1/6 | 1.3627 |
| 7 | 2023-12-05 | 2024-12-02 | 0.4916 | 0.5451 | 0.6746 | 0.2917 | 0.7786 | 0.0000 | 3/8 | 1.9674 |
| 8 | 2024-12-03 | 2025-12-02 | 0.5048 | 0.4531 | 0.6083 | 0.2775 | 0.7489 | 0.0000 | 1/6 | 1.3494 |

| Min | Q25 | Median | Q75 | Max | Mean | Std | Fraction >= 0.5 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.3461 | 0.3971 | 0.5454 | 0.5901 | 0.7254 | 0.5120 | 0.1048 | 0.5836 |

Training total: 12.67 seconds. Device(s): ['cpu']. Trainable parameters: 57473. Final validation loss > train loss + 0.15 in 8/9 folds. Final validation loss > best loss + 0.02 in 9/9 folds.

Test inference per fold (250 sequences): [18.67, 18.96, 18.79, 18.8, 18.8, 19.41, 19.29, 19.53, 19.11] ms. These timings include batched network inference and device transfers; feature construction/scaling are excluded.

Probability buckets are descriptive calibration/outcome diagnostics, not calibrated predictions or trading returns.

| lower | upper | upper_inclusive | observations | mean_probability | event_frequency | mean_future_log_return | mean_future_simple_return |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0.0 | 0.4 | False | 655 | 0.38455 | 0.58473 | 0.0029 | 0.00304 |
| 0.4 | 0.45 | False | 226 | 0.41392 | 0.62389 | 0.00416 | 0.00442 |
| 0.45 | 0.5 | False | 56 | 0.47886 | 0.69643 | 0.01336 | 0.01368 |
| 0.5 | 0.55 | False | 209 | 0.52734 | 0.59809 | 0.00406 | 0.00443 |
| 0.55 | 0.6 | False | 682 | 0.58019 | 0.51613 | 0.00111 | 0.00141 |
| 0.6 | 1.0 | True | 422 | 0.64911 | 0.60664 | 0.00248 | 0.00298 |

Walk-forward artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/SPY/1d/h5/walk_forward/20260910T152222_525ff4c4`

Backtest artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/SPY/1d/h5/walk_forward/20260910T152222_525ff4c4/backtest/20260910T152237_0199084b`

## Single-split smoke test

Artifact: `/Users/lucas/Documents/LucasProject/just-looking-for-stocks/artifacts/AAPL/1d/h5/20260910T152115_ae6dca99/report.json`. This separate 70/15/15 run retrains the baseline models solely for the requested smoke comparison. It does not replace any frozen walk-forward baseline.

| Model | Test AUC | Test AP | Brier | Log loss | Accuracy | Balanced accuracy | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.5301 | 0.4699 | 16.9385 | 0.5301 | 0.5000 | 0.6929 |
| training_prior | 0.5000 | 0.5301 | 0.2498 | 0.6927 | 0.5301 | 0.5000 | 0.6929 |
| momentum | 0.5070 | 0.5336 | 0.4900 | 17.6607 | 0.5100 | 0.5070 | 0.5464 |
| logistic | 0.4939 | 0.5553 | 0.2690 | 0.7366 | 0.4954 | 0.4869 | 0.5692 |
| xgboost | 0.5633 | 0.5719 | 0.2481 | 0.6893 | 0.5301 | 0.5000 | 0.6929 |
| gru | 0.4970 | 0.5067 | 0.2503 | 0.6937 | 0.5191 | 0.5071 | 0.6095 |

## Limits

The GRU is a challenger, not an automatically preferred model. Thirty-six fold fits on two related US assets do not constitute independent replication. Current results must not be used to choose a new threshold, architecture or feature set and then described as unseen evidence.

The binary model cannot supply a valid exact-price forecast. TensorFlow/Keras remains temporarily for the legacy `/api/predict` price contract; see `docs/stage6-legacy-migration.md`. No ensemble was built.
