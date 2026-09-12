"""Predeclared outcome buckets are diagnostics, never fitting or decision inputs."""
import numpy as np
import pandas as pd


def probability_buckets(oof: pd.DataFrame, model: str, edges: tuple[float, ...]) -> pd.DataFrame:
    probability = oof[f"{model}_probability"]
    rows = []
    for i, (low, high) in enumerate(zip(edges, edges[1:])):
        mask = (probability >= low) & ((probability <= high) if i == len(edges)-2 else (probability < high))
        part = oof.loc[mask]
        rows.append({"lower": low, "upper": high, "upper_inclusive": i == len(edges)-2,
                     "observations": len(part), "mean_probability": float(probability[mask].mean()) if len(part) else None,
                     "event_frequency": float(part.actual_target.mean()) if len(part) else None,
                     "mean_future_log_return": float(part.future_log_return.mean()) if len(part) else None,
                     "mean_future_simple_return": float(np.expm1(part.future_log_return).mean()) if len(part) else None})
    return pd.DataFrame(rows)


def estimated_cost_break_even(gross_trades: pd.DataFrame) -> float | None:
    """First-order bps per side: zero-cost dollar PnL / two-sided raw notional.

    Holds zero-cost quantities fixed; ignores resizing/compounding under costs.
    Not a fitted strategy parameter or a cost budget relative to Buy & Hold.
    """
    if gross_trades.empty:
        return None
    pnl = float(gross_trades.gross_pnl.sum())
    notional = float((gross_trades.quantity * (gross_trades.raw_entry_price + gross_trades.raw_exit_price)).sum())
    return max(pnl, 0.0) / notional * 10000 if notional > 0 else None
