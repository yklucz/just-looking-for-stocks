"""Reproducible synthetic input and descriptive timings; no model/market claims.

Run from the repository root: python -m scripts.benchmark_features
"""
import json
from time import perf_counter

import numpy as np
import pandas as pd

from stock_app.features import build_features


def synthetic_history(rows: int) -> pd.DataFrame:
    rng = np.random.default_rng(73)
    close = 100 * np.exp(np.cumsum(rng.normal(.00002, .01, rows)))
    opening = close * np.exp(rng.normal(0, .002, rows))
    return pd.DataFrame({"Open": opening, "High": np.maximum(opening, close) * 1.01,
                         "Low": np.minimum(opening, close) * .99, "Close": close,
                         "Volume": rng.integers(1000, 10000, rows).astype(float)},
                        index=pd.bdate_range("2020-01-01", periods=rows))


def main() -> None:
    for rows in (1000, 20000):
        history = synthetic_history(rows)
        times = []
        for _ in range(3):
            started = perf_counter()
            result = build_features(history)
            times.append(perf_counter() - started)
        print(json.dumps({"dataset": "synthetic OHLCV seed=73", "input_rows": rows,
                          "feature_count": len(result.feature_names),
                          "min_startup_candles": result.max_lookback,
                          "warmup_rows": result.warmup_rows, "usable_rows": len(result.frame),
                          "seconds_min": min(times), "seconds_median": float(np.median(times)),
                          "seconds_max": max(times), "feature_names": result.feature_names}))


if __name__ == "__main__":
    main()
