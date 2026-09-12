"""Causal synthetic market: volume[t] may drive return[t+1], never the reverse."""
import numpy as np
import pandas as pd


def synthetic_market(signal: bool, rows: int = 3000, seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    state = rng.choice([-1, 1], rows)
    volume = np.where(state > 0, 100000, 1000) + rng.integers(1, 50, rows)
    innovations = rng.normal(0, .006, rows)
    if signal:
        innovations[1:] += .025 * state[:-1]
    close = 100 * np.exp(np.cumsum(innovations))
    opening = close * np.exp(rng.normal(0, .001, rows))
    return pd.DataFrame({"Open": opening, "High": np.maximum(opening, close)*1.01,
                         "Low": np.minimum(opening, close)*.99, "Close": close,
                         "Volume": volume}, index=pd.bdate_range("2000-01-01", periods=rows))
