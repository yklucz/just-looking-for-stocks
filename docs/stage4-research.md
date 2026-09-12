# Stage 4 research results — 2026-09-10

XGBoost did not consistently outperform the naive references. Its ranking was near chance across nine periods for AAPL and SPY. Rolling windows improved several summaries modestly, but these experiments establish neither durable predictive edge nor trading profitability. Logistic regression also failed to generalize reliably.

## Protocol and provenance

Target fixed before experiments: `log(Close[t+5] / Close[t]) > 0.002`; binary probability cutoff 0.5. All five Stage 3 models and their defaults were retained (seed 42; XGBoost 200 maximum estimators, depth 3, learning rate .05, validation log-loss early stopping with patience 20). No parameter or threshold tuning used these test results.

Both assets supplied 3,692 daily candles. Full features: 49 columns, 199 warm-up rows, 3,493 feature-valid rows and 3,488 aligned labeled rows. Raw window budgets: initial train 1,000, validation 250, test 250, step 250, rolling train 1,000. Each experiment has 9 folds and 2,250 unique test origins from 2016-12-20 to 2025-12-02; the final outcome matures 2025-12-09. The remaining 187 labeled origins do not form a complete test window and are explicitly excluded. No inference about more recent dates is reported.

Actual expanding training sizes: 796, 1,046, 1,296, 1,546, 1,796, 2,046, 2,296, 2,546, 2,796. Rolling sizes: 796 initially, then 995. Every validation has 245 origins after five-row purging, every test 250. Counts use complete causal feature rows.

A first batch rejected a SPY download for an OHLC relationship violation. A fresh download passed the same unchanged validation and supplied the completed SPY results below; MSFT fallback was not needed. The first rejected snapshot was not retained. The batch runner now retains rejected OHLC snapshots and supports fallback for subsequent runs. Earlier partial-batch AAPL artifacts remain untouched; this report uses only the five run IDs below. No bad prices were repaired to force acceptance.

## Run comparison


| Run | Folds | Mean XGB AUC | AUC std | Pooled AUC | Pooled AP | XGB Brier | Prior Brier | AUC > .5 | AP > prevalence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL_expanding | 9 | 0.5055 | 0.0363 | 0.4863 | 0.5561 | 0.2492 | 0.2482 | 6/9 | 7/9 |
| AAPL_rolling | 9 | 0.5119 | 0.0369 | 0.5151 | 0.5769 | 0.2480 | 0.2497 | 5/9 | 6/9 |
| AAPL_no_sma200_matched | 9 | 0.5015 | 0.0369 | 0.4987 | 0.5544 | 0.2490 | 0.2482 | 5/9 | 6/9 |
| SPY_expanding | 9 | 0.5007 | 0.0395 | 0.4685 | 0.5482 | 0.2581 | 0.2447 | 4/9 | 5/9 |
| SPY_rolling | 9 | 0.5123 | 0.0440 | 0.4861 | 0.5710 | 0.2505 | 0.2458 | 5/9 | 5/9 |


Lower Brier is better. AP denotes average precision. Mean fold AUC and pooled AUC are distinct: pooling introduces cross-period score/prior comparisons. Constant-prior baselines have within-fold AUC .5 but can have pooled AUC different from .5. No mode is declared universally superior.

## AAPL_expanding


| Model | Mean AUC | AUC std | Pooled AUC | Pooled AP | Pooled F1 | Pooled Brier | Brier wins vs prior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.0000 | 0.5000 | 0.5644 | 0.7216 | 0.4356 | 0 |
| training_prior | 0.5000 | 0.0000 | 0.4626 | 0.5434 | 0.7216 | 0.2482 | reference |
| momentum | 0.5047 | 0.0232 | 0.5080 | 0.5684 | 0.5584 | 0.4871 | 0 |
| logistic | 0.5205 | 0.0624 | 0.4960 | 0.5526 | 0.3056 | 0.3721 | 0 |
| xgboost | 0.5055 | 0.0363 | 0.4863 | 0.5561 | 0.6331 | 0.2492 | 5 |



| Fold | Test origins | Train positive rate | Test positive rate | Majority AUC | Prior AUC | Momentum AUC | Logistic AUC | XGB AUC | XGB AP | XGB Brier | Selected trees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-12-20–2017-12-15 | 0.5063 | 0.6360 | 0.5000 | 0.5000 | 0.5193 | 0.5880 | 0.5443 | 0.6610 | 0.2519 | 1 |
| 2 | 2017-12-18–2018-12-14 | 0.5048 | 0.5080 | 0.5000 | 0.5000 | 0.5117 | 0.6144 | 0.5099 | 0.5131 | 0.2502 | 1 |
| 3 | 2018-12-17–2019-12-12 | 0.5301 | 0.6600 | 0.5000 | 0.5000 | 0.4579 | 0.5000 | 0.5221 | 0.6704 | 0.2403 | 1 |
| 4 | 2019-12-13–2020-12-09 | 0.5298 | 0.6520 | 0.5000 | 0.5000 | 0.5209 | 0.5625 | 0.4475 | 0.6193 | 0.2430 | 2 |
| 5 | 2020-12-10–2021-12-07 | 0.5451 | 0.5720 | 0.5000 | 0.5000 | 0.4749 | 0.5532 | 0.5406 | 0.6030 | 0.2443 | 2 |
| 6 | 2021-12-08–2022-12-05 | 0.5591 | 0.4280 | 0.5000 | 0.5000 | 0.5297 | 0.3964 | 0.5020 | 0.4290 | 0.2592 | 1 |
| 7 | 2022-12-06–2023-12-04 | 0.5597 | 0.5760 | 0.5000 | 0.5000 | 0.4925 | 0.4984 | 0.4439 | 0.5507 | 0.2569 | 85 |
| 8 | 2023-12-05–2024-12-02 | 0.5483 | 0.5240 | 0.5000 | 0.5000 | 0.5097 | 0.4793 | 0.4939 | 0.5481 | 0.2494 | 2 |
| 9 | 2024-12-03–2025-12-02 | 0.5494 | 0.5240 | 0.5000 | 0.5000 | 0.5258 | 0.4922 | 0.5450 | 0.5919 | 0.2475 | 17 |


### Full pooled OOF metrics


| Metric | majority | training_prior | momentum | logistic | xgboost |
| --- | --- | --- | --- | --- | --- |
| accuracy | 0.5644 | 0.5644 | 0.5129 | 0.4507 | 0.5271 |
| balanced_accuracy | 0.5000 | 0.5000 | 0.5080 | 0.4857 | 0.4982 |
| precision | 0.5644 | 0.5644 | 0.5718 | 0.5333 | 0.5632 |
| recall | 1.0000 | 1.0000 | 0.5457 | 0.2142 | 0.7228 |
| f1 | 0.7216 | 0.7216 | 0.5584 | 0.3056 | 0.6331 |
| roc_auc | 0.5000 | 0.4626 | 0.5080 | 0.4960 | 0.4863 |
| pr_auc | 0.5644 | 0.5434 | 0.5684 | 0.5526 | 0.5561 |
| brier_score | 0.4356 | 0.2482 | 0.4871 | 0.3721 | 0.2492 |
| log_loss | 15.6990 | 0.6895 | 17.5573 | 1.1146 | 0.6915 |
| directional_accuracy | 0.5924 | 0.5924 | 0.5213 | 0.4422 | 0.5373 |


### XGBoost aggregate test metrics


| Metric | Mean | Median | Population std | Minimum | Maximum |
| --- | --- | --- | --- | --- | --- |
| accuracy | 0.5271 | 0.5240 | 0.0828 | 0.4160 | 0.6600 |
| balanced_accuracy | 0.4990 | 0.5000 | 0.0170 | 0.4558 | 0.5221 |
| precision | 0.5147 | 0.5400 | 0.2002 | 0.0000 | 0.7241 |
| recall | 0.7235 | 1.0000 | 0.3786 | 0.0000 | 1.0000 |
| f1 | 0.5576 | 0.6446 | 0.2556 | 0.0000 | 0.7952 |
| roc_auc | 0.5055 | 0.5099 | 0.0363 | 0.4439 | 0.5450 |
| pr_auc | 0.5763 | 0.5919 | 0.0715 | 0.4290 | 0.6704 |
| brier_score | 0.2492 | 0.2494 | 0.0059 | 0.2403 | 0.2592 |
| log_loss | 0.6915 | 0.6919 | 0.0118 | 0.6737 | 0.7116 |
| directional_accuracy | 0.5373 | 0.5560 | 0.0968 | 0.4040 | 0.6920 |


### Test probability distributions


| Model | Fold | Minimum | Q25 | Median | Q75 | Maximum | Mean | Std | UP fraction |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| logistic | 1 | 0.0282 | 0.1466 | 0.2167 | 0.2845 | 0.6336 | 0.2169 | 0.1053 | 0.0120 |
| logistic | 2 | 0.0022 | 0.0214 | 0.0376 | 0.0757 | 0.3068 | 0.0556 | 0.0484 | 0.0000 |
| logistic | 3 | 0.0300 | 0.0790 | 0.1431 | 0.1993 | 0.5339 | 0.1505 | 0.0818 | 0.0040 |
| logistic | 4 | 0.0025 | 0.0467 | 0.1296 | 0.3241 | 0.7843 | 0.1884 | 0.1708 | 0.0680 |
| logistic | 5 | 0.0029 | 0.0694 | 0.1577 | 0.2789 | 0.6292 | 0.1905 | 0.1465 | 0.0480 |
| logistic | 6 | 0.0201 | 0.0907 | 0.1873 | 0.3874 | 0.7487 | 0.2529 | 0.1952 | 0.1440 |
| logistic | 7 | 0.2503 | 0.5147 | 0.6258 | 0.7111 | 0.8205 | 0.6040 | 0.1319 | 0.7800 |
| logistic | 8 | 0.1505 | 0.4432 | 0.4989 | 0.5549 | 0.7659 | 0.4986 | 0.0952 | 0.4880 |
| logistic | 9 | 0.0662 | 0.4012 | 0.4982 | 0.5905 | 0.7692 | 0.4902 | 0.1334 | 0.4960 |
| xgboost | 1 | 0.4864 | 0.4864 | 0.4864 | 0.4977 | 0.5081 | 0.4913 | 0.0076 | 0.1160 |
| xgboost | 2 | 0.4849 | 0.4849 | 0.4849 | 0.4972 | 0.4972 | 0.4897 | 0.0060 | 0.0000 |
| xgboost | 3 | 0.5298 | 0.5298 | 0.5298 | 0.5417 | 0.5417 | 0.5332 | 0.0054 | 1.0000 |
| xgboost | 4 | 0.5119 | 0.5255 | 0.5255 | 0.5324 | 0.5507 | 0.5283 | 0.0064 | 1.0000 |
| xgboost | 5 | 0.5390 | 0.5505 | 0.5629 | 0.5716 | 0.5838 | 0.5605 | 0.0143 | 1.0000 |
| xgboost | 6 | 0.5379 | 0.5379 | 0.5525 | 0.5525 | 0.5525 | 0.5478 | 0.0068 | 1.0000 |
| xgboost | 7 | 0.3175 | 0.4716 | 0.5118 | 0.5526 | 0.6991 | 0.5110 | 0.0646 | 0.6000 |
| xgboost | 8 | 0.5028 | 0.5151 | 0.5151 | 0.5314 | 0.5585 | 0.5241 | 0.0126 | 1.0000 |
| xgboost | 9 | 0.4474 | 0.5023 | 0.5167 | 0.5347 | 0.5715 | 0.5178 | 0.0235 | 0.8040 |


XGBoost predicted every row UP in five folds and every row NOT-UP in one fold. The other three folds had mixed predictions. Selected tree counts were 1, 1, 1, 2, 2, 1, 85, 2, 17. Small score shifts around .5 explain abrupt changes in class predictions; ranking performance and threshold behavior are separate diagnostics. The cutoff was not optimized.

### Market-period diagnostics (not trading returns)


| Fold | Mean volatility_20 | Mean observed return_1 | Mean future five-candle log return |
| --- | --- | --- | --- |
| 1 | 0.0103 | 0.0017 | 0.0084 |
| 2 | 0.0155 | 0.0000 | -0.0016 |
| 3 | 0.0168 | 0.0022 | 0.0115 |
| 4 | 0.0259 | 0.0028 | 0.0118 |
| 5 | 0.0151 | 0.0015 | 0.0070 |
| 6 | 0.0217 | -0.0003 | -0.0041 |
| 7 | 0.0138 | 0.0011 | 0.0062 |
| 8 | 0.0138 | 0.0011 | 0.0047 |
| 9 | 0.0179 | 0.0009 | 0.0029 |


### Feature stability


| Feature | Mean gain | Median rank | Top-10 folds |
| --- | --- | --- | --- |
| volume_mean_20 | 17.2726 | 3.0 | 9/9 |
| ema_10_ema_50_ratio | 11.9546 | 4.0 | 7/9 |
| sma_20 | 14.3828 | 3.0 | 5/9 |
| volume_mean_5 | 8.3028 | 27.0 | 4/9 |
| macd | 4.6731 | 25.0 | 3/9 |
| ema_20 | 6.6754 | 27.0 | 3/9 |
| macd_signal | 2.8349 | 27.0 | 3/9 |
| month_sin | 4.8120 | 27.0 | 3/9 |
| sma_5 | 7.3880 | 27.0 | 3/9 |
| ema_10 | 6.3444 | 27.5 | 3/9 |


Gain is averaged across selected boosting trees and then folds, without cross-fold normalization. Zero-gain features do not count toward top-10 appearances; ties use average ranks. These scores do not prove causal influence or predictive usefulness.

## AAPL_rolling


| Model | Mean AUC | AUC std | Pooled AUC | Pooled AP | Pooled F1 | Pooled Brier | Brier wins vs prior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.0000 | 0.5000 | 0.5644 | 0.7216 | 0.4356 | 0 |
| training_prior | 0.5000 | 0.0000 | 0.4512 | 0.5283 | 0.7216 | 0.2497 | reference |
| momentum | 0.5047 | 0.0232 | 0.5080 | 0.5684 | 0.5584 | 0.4871 | 0 |
| logistic | 0.5304 | 0.0750 | 0.4951 | 0.5521 | 0.3517 | 0.3830 | 0 |
| xgboost | 0.5119 | 0.0369 | 0.5151 | 0.5769 | 0.6374 | 0.2480 | 6 |



| Fold | Test origins | Train positive rate | Test positive rate | Majority AUC | Prior AUC | Momentum AUC | Logistic AUC | XGB AUC | XGB AP | XGB Brier | Selected trees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-12-20–2017-12-15 | 0.5063 | 0.6360 | 0.5000 | 0.5000 | 0.5193 | 0.5880 | 0.5443 | 0.6610 | 0.2519 | 1 |
| 2 | 2017-12-18–2018-12-14 | 0.5146 | 0.5080 | 0.5000 | 0.5000 | 0.5117 | 0.6406 | 0.4737 | 0.5006 | 0.2504 | 1 |
| 3 | 2018-12-17–2019-12-12 | 0.5497 | 0.6600 | 0.5000 | 0.5000 | 0.4579 | 0.5442 | 0.4939 | 0.6573 | 0.2364 | 1 |
| 4 | 2019-12-13–2020-12-09 | 0.5357 | 0.6520 | 0.5000 | 0.5000 | 0.5209 | 0.5886 | 0.5853 | 0.6957 | 0.2387 | 4 |
| 5 | 2020-12-10–2021-12-07 | 0.5769 | 0.5720 | 0.5000 | 0.5000 | 0.4749 | 0.5363 | 0.5340 | 0.5835 | 0.2439 | 2 |
| 6 | 2021-12-08–2022-12-05 | 0.6141 | 0.4280 | 0.5000 | 0.5000 | 0.5297 | 0.3659 | 0.4817 | 0.4439 | 0.2587 | 33 |
| 7 | 2022-12-06–2023-12-04 | 0.5960 | 0.5760 | 0.5000 | 0.5000 | 0.4925 | 0.4978 | 0.5050 | 0.5921 | 0.2535 | 46 |
| 8 | 2023-12-05–2024-12-02 | 0.5799 | 0.5240 | 0.5000 | 0.5000 | 0.5097 | 0.4730 | 0.5261 | 0.5542 | 0.2491 | 3 |
| 9 | 2024-12-03–2025-12-02 | 0.5548 | 0.5240 | 0.5000 | 0.5000 | 0.5258 | 0.5394 | 0.4631 | 0.5195 | 0.2498 | 1 |


## AAPL_no_sma200_matched


| Model | Mean AUC | AUC std | Pooled AUC | Pooled AP | Pooled F1 | Pooled Brier | Brier wins vs prior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.0000 | 0.5000 | 0.5644 | 0.7216 | 0.4356 | 0 |
| training_prior | 0.5000 | 0.0000 | 0.4626 | 0.5434 | 0.7216 | 0.2482 | reference |
| momentum | 0.5047 | 0.0232 | 0.5080 | 0.5684 | 0.5584 | 0.4871 | 0 |
| logistic | 0.5260 | 0.0681 | 0.4943 | 0.5453 | 0.3067 | 0.3528 | 0 |
| xgboost | 0.5015 | 0.0369 | 0.4987 | 0.5544 | 0.6529 | 0.2490 | 5 |



| Fold | Test origins | Train positive rate | Test positive rate | Majority AUC | Prior AUC | Momentum AUC | Logistic AUC | XGB AUC | XGB AP | XGB Brier | Selected trees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-12-20–2017-12-15 | 0.5063 | 0.6360 | 0.5000 | 0.5000 | 0.5193 | 0.6175 | 0.5443 | 0.6610 | 0.2519 | 1 |
| 2 | 2017-12-18–2018-12-14 | 0.5048 | 0.5080 | 0.5000 | 0.5000 | 0.5117 | 0.6236 | 0.5099 | 0.5131 | 0.2502 | 1 |
| 3 | 2018-12-17–2019-12-12 | 0.5301 | 0.6600 | 0.5000 | 0.5000 | 0.4579 | 0.4934 | 0.5221 | 0.6704 | 0.2403 | 1 |
| 4 | 2019-12-13–2020-12-09 | 0.5298 | 0.6520 | 0.5000 | 0.5000 | 0.5209 | 0.5866 | 0.4475 | 0.6193 | 0.2430 | 2 |
| 5 | 2020-12-10–2021-12-07 | 0.5451 | 0.5720 | 0.5000 | 0.5000 | 0.4749 | 0.5485 | 0.4984 | 0.5850 | 0.2455 | 2 |
| 6 | 2021-12-08–2022-12-05 | 0.5591 | 0.4280 | 0.5000 | 0.5000 | 0.5297 | 0.4154 | 0.4615 | 0.4121 | 0.2612 | 4 |
| 7 | 2022-12-06–2023-12-04 | 0.5597 | 0.5760 | 0.5000 | 0.5000 | 0.4925 | 0.5043 | 0.4533 | 0.5363 | 0.2507 | 47 |
| 8 | 2023-12-05–2024-12-02 | 0.5483 | 0.5240 | 0.5000 | 0.5000 | 0.5097 | 0.4789 | 0.5523 | 0.5745 | 0.2486 | 2 |
| 9 | 2024-12-03–2025-12-02 | 0.5494 | 0.5240 | 0.5000 | 0.5000 | 0.5258 | 0.4653 | 0.5240 | 0.5352 | 0.2493 | 7 |


## SPY_expanding


| Model | Mean AUC | AUC std | Pooled AUC | Pooled AP | Pooled F1 | Pooled Brier | Brier wins vs prior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.0000 | 0.5000 | 0.5760 | 0.7310 | 0.4240 | 0 |
| training_prior | 0.5000 | 0.0000 | 0.4822 | 0.5609 | 0.7310 | 0.2447 | reference |
| momentum | 0.4807 | 0.0271 | 0.4858 | 0.5692 | 0.5536 | 0.5053 | 0 |
| logistic | 0.4777 | 0.0548 | 0.4730 | 0.5551 | 0.4761 | 0.3165 | 0 |
| xgboost | 0.5007 | 0.0395 | 0.4685 | 0.5482 | 0.6475 | 0.2581 | 3 |



| Fold | Test origins | Train positive rate | Test positive rate | Majority AUC | Prior AUC | Momentum AUC | Logistic AUC | XGB AUC | XGB AP | XGB Brier | Selected trees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-12-20–2017-12-15 | 0.5666 | 0.5960 | 0.5000 | 0.5000 | 0.4781 | 0.4909 | 0.5583 | 0.6855 | 0.2603 | 10 |
| 2 | 2017-12-18–2018-12-14 | 0.5621 | 0.5280 | 0.5000 | 0.5000 | 0.4779 | 0.4541 | 0.4661 | 0.5137 | 0.2881 | 62 |
| 3 | 2018-12-17–2019-12-12 | 0.5671 | 0.6440 | 0.5000 | 0.5000 | 0.5341 | 0.5411 | 0.4368 | 0.6152 | 0.2349 | 1 |
| 4 | 2019-12-13–2020-12-09 | 0.5640 | 0.6080 | 0.5000 | 0.5000 | 0.5085 | 0.4838 | 0.4669 | 0.5836 | 0.2413 | 1 |
| 5 | 2020-12-10–2021-12-07 | 0.5724 | 0.5920 | 0.5000 | 0.5000 | 0.4385 | 0.4441 | 0.5607 | 0.6609 | 0.2395 | 15 |
| 6 | 2021-12-08–2022-12-05 | 0.5787 | 0.4360 | 0.5000 | 0.5000 | 0.4678 | 0.4109 | 0.4856 | 0.4413 | 0.2788 | 41 |
| 7 | 2022-12-06–2023-12-04 | 0.5788 | 0.5640 | 0.5000 | 0.5000 | 0.4872 | 0.5700 | 0.5173 | 0.5787 | 0.2460 | 2 |
| 8 | 2023-12-05–2024-12-02 | 0.5664 | 0.6240 | 0.5000 | 0.5000 | 0.4842 | 0.5106 | 0.5147 | 0.6883 | 0.2914 | 20 |
| 9 | 2024-12-03–2025-12-02 | 0.5647 | 0.5920 | 0.5000 | 0.5000 | 0.4498 | 0.3935 | 0.5000 | 0.5920 | 0.2428 | 1 |


## SPY_rolling


| Model | Mean AUC | AUC std | Pooled AUC | Pooled AP | Pooled F1 | Pooled Brier | Brier wins vs prior |
| --- | --- | --- | --- | --- | --- | --- | --- |
| majority | 0.5000 | 0.0000 | 0.5000 | 0.5760 | 0.7310 | 0.4240 | 0 |
| training_prior | 0.5000 | 0.0000 | 0.4614 | 0.5435 | 0.7310 | 0.2458 | reference |
| momentum | 0.4807 | 0.0271 | 0.4858 | 0.5692 | 0.5536 | 0.5053 | 0 |
| logistic | 0.5082 | 0.0432 | 0.4910 | 0.5769 | 0.4324 | 0.3560 | 0 |
| xgboost | 0.5123 | 0.0440 | 0.4861 | 0.5710 | 0.6554 | 0.2505 | 3 |



| Fold | Test origins | Train positive rate | Test positive rate | Majority AUC | Prior AUC | Momentum AUC | Logistic AUC | XGB AUC | XGB AP | XGB Brier | Selected trees |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 2016-12-20–2017-12-15 | 0.5666 | 0.5960 | 0.5000 | 0.5000 | 0.4781 | 0.4909 | 0.5583 | 0.6855 | 0.2603 | 10 |
| 2 | 2017-12-18–2018-12-14 | 0.5618 | 0.5280 | 0.5000 | 0.5000 | 0.4779 | 0.4597 | 0.4575 | 0.5224 | 0.2543 | 7 |
| 3 | 2018-12-17–2019-12-12 | 0.5508 | 0.6440 | 0.5000 | 0.5000 | 0.5341 | 0.5377 | 0.4768 | 0.6301 | 0.2373 | 1 |
| 4 | 2019-12-13–2020-12-09 | 0.5497 | 0.6080 | 0.5000 | 0.5000 | 0.5085 | 0.4872 | 0.4740 | 0.6028 | 0.2444 | 1 |
| 5 | 2020-12-10–2021-12-07 | 0.5759 | 0.5920 | 0.5000 | 0.5000 | 0.4385 | 0.4733 | 0.5230 | 0.6015 | 0.2451 | 19 |
| 6 | 2021-12-08–2022-12-05 | 0.5960 | 0.4360 | 0.5000 | 0.5000 | 0.4678 | 0.5574 | 0.5671 | 0.4715 | 0.2708 | 12 |
| 7 | 2022-12-06–2023-12-04 | 0.5920 | 0.5640 | 0.5000 | 0.5000 | 0.4872 | 0.5652 | 0.5559 | 0.5976 | 0.2429 | 26 |
| 8 | 2023-12-05–2024-12-02 | 0.5729 | 0.6240 | 0.5000 | 0.5000 | 0.4842 | 0.5553 | 0.5451 | 0.6780 | 0.2544 | 7 |
| 9 | 2024-12-03–2025-12-02 | 0.5487 | 0.5920 | 0.5000 | 0.5000 | 0.4498 | 0.4473 | 0.4531 | 0.5715 | 0.2449 | 1 |


## Model stability and baseline conclusions


| Run | Model | AUC std | Brier std | F1 std | Descriptive warnings |
| --- | --- | --- | --- | --- | --- |
| AAPL_expanding | logistic | 0.0624 | 0.0845 | 0.2422 | roc_auc, brier_score, f1 |
| AAPL_expanding | xgboost | 0.0363 | 0.0059 | 0.2556 | f1 |
| AAPL_rolling | logistic | 0.0750 | 0.0844 | 0.2247 | roc_auc, brier_score, f1 |
| AAPL_rolling | xgboost | 0.0369 | 0.0067 | 0.2054 | f1 |
| SPY_expanding | logistic | 0.0548 | 0.0573 | 0.2311 | roc_auc, brier_score, f1 |
| SPY_expanding | xgboost | 0.0395 | 0.0210 | 0.2866 | f1 |
| SPY_rolling | logistic | 0.0432 | 0.0822 | 0.2906 | brier_score, f1 |
| SPY_rolling | xgboost | 0.0440 | 0.0098 | 0.2438 | f1 |


AAPL expanding XGBoost had AUC above .5 in 6/9 folds (66.7%) and AP above prevalence in 7/9 (77.8%), yet pooled AUC was .4863 and Brier .2492 versus prior .2482. Its weakest AUCs were .4475 in 2019–20 and .4439 in 2022–23; the maximum was only .5450. This is near-chance variation, not a convincing edge concentrated in a few successful periods. XGBoost beat the training-prior Brier in 5/9 expanding and 6/9 rolling AAPL folds.

AAPL rolling XGBoost improved pooled AUC to .5151 and Brier to .2480 (prior .2497), with AUC above .5 in 5/9 folds. SPY expanding and rolling pooled AUCs remained .4685 and .4861; both lost to their training-prior Brier. Thus the second asset did not corroborate useful generalized probability estimates.

Logistic pooled AUC was .4960/.4951 on AAPL expanding/rolling and .4730/.4910 on SPY. Its Brier scores were substantially worse than the prior and XGBoost, and it beat prior Brier in zero folds in all four standard runs. A few above-chance fold AUCs did not translate into reliable pooled generalization.

Stability warnings use population std thresholds .05 for AUC, .03 for Brier, .15 for F1. They are descriptive alerts, not significance tests or model rankings. All selected models and feature configurations remain unchanged after observing these results.

## Controlled SMA200 ablation

Only `sma_200` was removed: 48 features, 60 warm-up rows, 3,632 feature-valid rows and 3,627 aligned rows. Training, validation and test origins were matched to the full feature set in every fold; full and short both retained 2,250 identical OOF origins. Hence extra available early history was intentionally not used.

Full versus short XGBoost mean fold AUC was .5055 versus .5015, pooled AUC .4863 versus .4987, and Brier .2492 versus .2490. These mixed changes provide no consistent justification for SMA200. Removing it makes 139 early rows available, but this matched experiment cannot quantify the benefit of using those rows. Do not select a final feature set based on this diagnostic test comparison and reuse the same periods as new confirmation.

## Synthetic pipeline validation

Five chronological folds per mode on 3,000 synthetic candles (seed 17, horizon 1). The planted causal relationship is Volume[t] influencing return[t+1]; the noise version removes that relationship.


| Dataset | Mode | Logistic pooled AUC | XGBoost pooled AUC |
| --- | --- | --- | --- |
| Planted signal | Expanding | 1.0000 | 1.0000 |
| Planted signal | Rolling | 1.0000 | 1.0000 |
| Independent noise | Expanding | .4824 | .4993 |
| Independent noise | Rolling | .4829 | .4906 |


Both learned models exceeded .9 AUC in every planted-signal fold, consistently beating the constant .5 within-fold baseline. Noise results were near chance within the predeclared [.35, .65] test bounds. These tests catch important pipeline failures; one seed is not an exhaustive statistical leakage proof.

## Artifacts and integrity checks

All five completed run manifests passed fingerprint and checksum validation (141 files each). All OOF timestamps were unique and chronological, all probabilities finite and bounded, every saved future log return/target/time matched the saved market snapshot, and model-selection cutoff labels preceded their test origin labels. Same-asset comparisons used identical raw-data hashes and OOF timestamps. Each run saves 45 independently fitted model artifacts.

OOF contains test rows only, with five probability/prediction pairs, ticker, fold ID, raw position, actual target, future log return, target time and fitting/selection cutoff. The validated full OHLCV snapshot is saved alongside it. Daily timestamps are candle labels, not executable timestamps: prediction and model-cutoff availability occur after the corresponding candle closes. Future outcome annotations must never become strategy inputs. Stage 5 must use subsequent-candle execution and can join the snapshot without retraining.


| Run | Saved folder |
| --- | --- |
| AAPL_expanding | [20260910T081655_ca5887a2](../artifacts/AAPL/1d/h5/walk_forward/20260910T081655_ca5887a2) |
| AAPL_rolling | [20260910T081657_5ad10c5f](../artifacts/AAPL/1d/h5/walk_forward/20260910T081657_5ad10c5f) |
| AAPL_no_sma200_matched | [20260910T081658_b1d8f9ac](../artifacts/AAPL/1d/h5/walk_forward/20260910T081658_b1d8f9ac) |
| SPY_expanding | [20260910T081700_fdf71a33](../artifacts/SPY/1d/h5/walk_forward/20260910T081700_fdf71a33) |
| SPY_rolling | [20260910T081702_8490f3d6](../artifacts/SPY/1d/h5/walk_forward/20260910T081702_8490f3d6) |


Comparison manifest: [stage4_comparison_1053262b.json](../artifacts/stage4_comparison_1053262b.json). Artifacts are local and gitignored; this report is retained with source documentation. CSV uses 17 significant digits and preserves timestamp offsets.

## Verification and remaining limits

Executed in `.venv311`: `python -m pytest -q -rs` → **162 passed, 1 skipped**. The unchanged Keras integration test skips because Keras is not installed in this environment. Compilation of `stock_app`, `tests`, and `scripts`, and `git diff --check` passed. Stage 4 adds 25 cases covering both fold modes, horizon purging, independent train-only scalers, fresh XGBoost instances and validation-only fitting, future-outlier invariance, explicit split validation, OOF uniqueness/alignment/checksums, matched ablation context, signal/noise and provider-failure fallback.

Historical adjusted data can be revised; this is not a point-in-time vendor archive. Adjacent five-candle labels overlap and fold results are not independent trials. There is no calibration, threshold optimization, statistical significance claim, trading backtest, transaction-cost model or live execution. The existing Flask frontend/backend and legacy predictor are unchanged by Stage 4.

**Proceeding to Stage 5 is justified as an engineering/research test of costs, execution assumptions and benchmark comparisons using frozen OOF predictions. It is not justified by demonstrated profitability or a validated forecasting edge.** Stage 5 was not started.
