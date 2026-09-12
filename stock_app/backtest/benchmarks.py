"""Benchmarks share the full OOF execution interval, irrespective of strategy trades."""
from dataclasses import replace

from ..config import BacktestConfig
from .engine import simulate


def benchmark_simulations(market, probabilities, config: BacktestConfig) -> dict:
    # Buy & Hold is the fully invested market reference, even for smaller strategy allocations.
    full = replace(config, position_size=1.0, maximum_position=1.0)
    return {name: simulate(market, probabilities, full, model=name, benchmark=name)
            for name in ("buy_hold", "cash")}
