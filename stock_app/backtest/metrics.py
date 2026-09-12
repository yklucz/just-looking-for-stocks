"""Open-to-open portfolio statistics, not annualized individual trade returns."""
import math

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from .engine import Simulation


def risk_statistics(returns: np.ndarray, annualization: int = 252) -> dict:
    r = np.asarray(returns, dtype=float)
    if not np.isfinite(r).all():
        raise ValueError("Returns must be finite")
    if len(r) < 2:
        return {"annualized_volatility": None, "sharpe": None, "sortino": None}
    std = float(np.std(r, ddof=1)) if len(r) > 1 else 0.0
    downside = float(np.sqrt(np.mean(np.minimum(r, 0)**2)))
    return {"annualized_volatility": std * math.sqrt(annualization),
            "sharpe": float(np.mean(r) / std * math.sqrt(annualization)) if std > 1e-15 else None,
            "sortino": float(np.mean(r) / downside * math.sqrt(annualization)) if downside > 1e-15 else None}


def performance(simulation: Simulation, config: BacktestConfig) -> dict:
    curve, trades = simulation.equity, simulation.trades
    total_return = float(curve.equity.iloc[-1] / config.initial_capital - 1)
    days = (curve.index[-1].date() - curve.index[0].date()).days
    years = days / 365.25
    cagr = (1 + total_return)**(1/years)-1 if years > 0 else None
    max_dd = float(curve.drawdown.min())
    pnl = trades.net_pnl.to_numpy(dtype=float)
    positive, negative = pnl[pnl > 0], pnl[pnl < 0]
    count = len(trades)
    raw_turnover = float(curve.turnover_notional.iloc[-1])
    fees, slip = float(curve.commission.iloc[-1]), float(curve.slippage_cost.iloc[-1])
    entry_positions = trades.entry_position.to_numpy(dtype=float)
    entry_dates = pd.to_datetime(trades.entry_timestamp, utc=True)
    gaps_days = entry_dates.diff().dt.total_seconds().dropna() / 86400
    return {"initial_capital": config.initial_capital, "final_equity": float(curve.equity.iloc[-1]),
            "total_return": total_return, "cagr": cagr,
            **risk_statistics(curve.period_return.iloc[1:].to_numpy(), config.annualization),
            "max_drawdown": max_dd, "calmar": cagr / abs(max_dd) if cagr is not None and max_dd < 0 else None,
            "win_rate": len(positive)/count if count else None,
            "loss_rate": len(negative)/count if count else None,
            "profit_factor": float(positive.sum()/abs(negative.sum())) if len(negative) else None,
            "average_win": float(positive.mean()) if len(positive) else None,
            "average_loss": float(negative.mean()) if len(negative) else None,
            "expectancy": float(pnl.mean()) if count else None,
            "trade_count": count, "average_holding_bars": float(trades.holding_bars.mean()) if count else None,
            "exposure": float(curve.invested.iloc[:-1].mean()) if len(curve) > 1 else 0.0,
            "average_capital_exposure": float(curve.exposure.iloc[:-1].mean()) if len(curve) > 1 else 0.0,
            "turnover": raw_turnover/config.initial_capital,
            "annualized_turnover": raw_turnover/config.initial_capital/years if years > 0 else None,
            "trades_per_year": count/years if years > 0 else None,
            "average_bars_between_entries": float(np.diff(entry_positions).mean()) if count > 1 else None,
            "average_days_between_entries": float(gaps_days.mean()) if count > 1 else None,
            "gross_pnl": float(trades.gross_pnl.sum()), "commission": fees, "slippage_cost": slip,
            "total_costs": fees+slip, "net_pnl": float(pnl.sum()),
            "evaluation_start": curve.index[0].isoformat(), "evaluation_end": curve.index[-1].isoformat(),
            "sessions": len(curve)-1, "calendar_years": years}
