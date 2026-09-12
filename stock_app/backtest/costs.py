"""Adverse fill prices; commission is separate and charged on effective notional."""
from ..config import BacktestConfig


def effective_price(raw_price: float, side: str, config: BacktestConfig) -> float:
    if side not in {"entry", "exit"}:
        raise ValueError("Unknown execution side")
    return raw_price * (1 + (1 if side == "entry" else -1) * config.slippage_bps / 10000)


def commission(notional: float, config: BacktestConfig) -> float:
    return notional * config.commission_bps / 10000
