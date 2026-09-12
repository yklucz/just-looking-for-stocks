"""Chronological open-to-open fills and marks; only origin probabilities enter policy."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from .execution import execution_positions
from .portfolio import Portfolio
from .strategy import LongFlatStrategy


TRADE_COLUMNS = (
    "trade_id",
    "model",
    "signal_origin",
    "signal_position",
    "entry_timestamp",
    "entry_position",
    "exit_timestamp",
    "exit_position",
    "planned_exit_position",
    "model_probability",
    "decision_threshold",
    "quantity",
    "raw_entry_price",
    "effective_entry_price",
    "raw_exit_price",
    "effective_exit_price",
    "position_value",
    "entry_outlay",
    "entry_commission",
    "exit_commission",
    "commission",
    "slippage_cost",
    "gross_pnl",
    "net_pnl",
    "gross_return",
    "net_return",
    "return_pct",
    "holding_bars",
)


@dataclass
class Simulation:
    equity: pd.DataFrame
    trades: pd.DataFrame
    diagnostics: dict[str, int]


@dataclass
class SignalCounters:
    ignored: int = 0
    incomplete: int = 0
    flat: int = 0


def evaluation_bounds(
    market: pd.DataFrame,
    probabilities: pd.Series,
    holding_period: int,
) -> tuple[int, int]:
    """Return the inclusive market positions used for evaluation."""

    positions = market.index.get_indexer(probabilities.index)

    if len(positions) == 0 or (positions < 0).any():
        raise ValueError("Signals must align with market timestamps")

    start = int(positions[0]) + 1
    end = min(
        int(positions[-1]) + 1 + holding_period,
        len(market) - 1,
    )

    if start > end:
        raise ValueError("No next-bar execution period exists")

    return start, end


def _validate_inputs(
    market: pd.DataFrame,
    probabilities: pd.Series,
    benchmark: str | None,
) -> None:
    """Validate chronological market data and model probabilities."""

    valid_market_index = (
        isinstance(market.index, pd.DatetimeIndex)
        and market.index.is_unique
        and market.index.is_monotonic_increasing
        and not market.index.hasnans
    )

    valid_probability_index = (
        probabilities.index.is_unique
        and probabilities.index.is_monotonic_increasing
    )

    if not valid_market_index or not valid_probability_index:
        raise ValueError(
            "Chronological unique market and signal indexes required"
        )

    if "Open" not in market:
        raise ValueError("Finite positive Open prices required")

    open_prices = market["Open"]

    if (
        not np.isfinite(open_prices).all()
        or (open_prices <= 0).any()
    ):
        raise ValueError("Finite positive Open prices required")

    if (
        not np.isfinite(probabilities).all()
        or ((probabilities < 0) | (probabilities > 1)).any()
    ):
        raise ValueError("Invalid probabilities")

    if benchmark not in {None, "buy_hold", "cash"}:
        raise ValueError("Unknown benchmark")


def _build_origins(
    market: pd.DataFrame,
    probabilities: pd.Series,
) -> dict[int, float]:
    """
    Map market positions to probabilities.

    Explicit int() conversion avoids np.intp versus int type issues
    reported by Pylance.
    """

    positions = market.index.get_indexer(probabilities.index)

    return {
        int(position): float(probability)
        for position, probability in zip(
            positions,
            probabilities.to_numpy(),
        )
    }


def _consider_signal(
    origin: int,
    *,
    origins: dict[int, float],
    market: pd.DataFrame,
    portfolio: Portfolio,
    policy: LongFlatStrategy,
    config: BacktestConfig,
    model: str,
    trade_id: int,
    end: int,
    counters: SignalCounters,
) -> dict | None:
    """Evaluate one signal origin and construct a pending trade."""

    if origin not in origins:
        return None

    if portfolio.position is not None:
        counters.ignored += 1
        return None

    probability = origins[origin]

    if (
        policy.generate_signal(probability) == "FLAT"
        or config.position_size == 0
    ):
        counters.flat += 1
        return None

    entry, exit_position = execution_positions(
        origin,
        config.holding_period,
    )

    if exit_position >= len(market) or exit_position > end:
        counters.incomplete += 1
        return None

    return {
        "trade_id": trade_id,
        "model": model,
        "signal_origin": market.index[origin],
        "signal_position": origin,
        "entry_timestamp": market.index[entry],
        "entry_position": entry,
        "planned_exit_position": exit_position,
        "model_probability": probability,
        "decision_threshold": config.decision_threshold,
    }


def _exit_position_if_due(
    portfolio: Portfolio,
    trades: list[dict],
    price: float,
    timestamp: pd.Timestamp,
    position: int,
) -> None:
    """Exit the current position when its planned exit bar is reached."""

    current_position = portfolio.position

    if current_position is None:
        return

    if current_position["planned_exit_position"] != position:
        return

    trade = portfolio.exit(
        price,
        timestamp,
        position,
    )

    trades.append(trade)


def _enter_pending_trade(
    portfolio: Portfolio,
    pending: dict | None,
    price: float,
) -> None:
    """Enter a pending trade at the current open."""

    if pending is None:
        return

    portfolio.enter(
        price,
        pending,
    )


def _enter_buy_hold_if_due(
    portfolio: Portfolio,
    *,
    market: pd.DataFrame,
    config: BacktestConfig,
    price: float,
    position: int,
    start: int,
    end: int,
) -> None:
    """Enter the benchmark buy-and-hold position at the first open."""

    if position != start:
        return

    if end <= start:
        return

    if config.position_size <= 0:
        return

    portfolio.enter(
        price,
        {
            "trade_id": 1,
            "model": "buy_hold",
            "signal_origin": market.index[start - 1],
            "signal_position": start - 1,
            "entry_timestamp": market.index[start],
            "entry_position": start,
            "planned_exit_position": end,
            "model_probability": None,
            "decision_threshold": None,
        },
    )


def _build_equity_curve(
    marks: list[dict],
    config: BacktestConfig,
) -> pd.DataFrame:
    """Build returns, running peak, and drawdown columns."""

    curve = pd.DataFrame(marks).set_index("timestamp")

    curve["running_peak"] = (
        curve["equity"]
        .cummax()
        .clip(lower=config.initial_capital)
    )

    curve["drawdown"] = (
        curve["equity"] / curve["running_peak"] - 1
    )

    curve["period_return"] = curve["equity"].pct_change()

    # First interval begins with pre-entry capital.
    # This includes initial execution costs in the first measured return.
    if len(curve) > 1:
        second_timestamp = curve.index[1]

        curve.at[second_timestamp, "period_return"] = (
            curve["equity"].iloc[1]
            / config.initial_capital
            - 1
        )

    return curve


def _unsupported_exit_origins(
    origins: dict[int, float],
    market_length: int,
    holding_period: int,
) -> int:
    """Count signals whose planned exits would exceed available market data."""

    return sum(
        origin + 1 + holding_period >= market_length
        for origin in origins
    )


def _build_diagnostics(
    *,
    counters: SignalCounters,
    origins: dict[int, float],
    market_length: int,
    holding_period: int,
    start: int,
    end: int,
) -> dict[str, int]:
    """Build simulation diagnostic statistics."""

    return {
        "ignored_while_invested": counters.ignored,
        "incomplete_long_signals_excluded": counters.incomplete,
        "flat_signals": counters.flat,
        "unsupported_exit_origins": _unsupported_exit_origins(
            origins,
            market_length,
            holding_period,
        ),
        "evaluation_start_position": start,
        "evaluation_end_position": end,
    }


def simulate(
    market: pd.DataFrame,
    probabilities: pd.Series,
    config: BacktestConfig,
    model: str = "strategy",
    benchmark: str | None = None,
) -> Simulation:
    """
    Run a chronological open-to-open simulation.

    Probabilities generated from candle i are only considered after the
    open mark for candle i. Therefore, a signal originating from candle i
    can first execute at the open of candle i + 1.

    Signals generated while already invested are ignored permanently.
    """

    _validate_inputs(
        market,
        probabilities,
        benchmark,
    )

    start, end = evaluation_bounds(
        market,
        probabilities,
        config.holding_period,
    )

    origins = _build_origins(
        market,
        probabilities,
    )

    portfolio = Portfolio(config)
    policy = LongFlatStrategy(config.decision_threshold)

    marks: list[dict] = []
    trades: list[dict] = []

    counters = SignalCounters()

    pending: dict | None = None

    # Probability from the candle immediately before the evaluation
    # window can generate an entry at the first evaluation open.
    if benchmark is None:
        pending = _consider_signal(
            start - 1,
            origins=origins,
            market=market,
            portfolio=portfolio,
            policy=policy,
            config=config,
            model=model,
            trade_id=1,
            end=end,
            counters=counters,
        )

    for position in range(start, end + 1):
        timestamp = market.index[position]
        price = float(market["Open"].iloc[position])

        # Exit first.
        #
        # A signal generated while the strategy was invested on a
        # previous close cannot later be resurrected after this exit.
        _exit_position_if_due(
            portfolio,
            trades,
            price,
            timestamp,
            position,
        )

        # A signal generated on the previous candle closes into an
        # entry at the current candle's open.
        _enter_pending_trade(
            portfolio,
            pending,
            price,
        )

        pending = None

        if benchmark == "buy_hold":
            _enter_buy_hold_if_due(
                portfolio,
                market=market,
                config=config,
                price=price,
                position=position,
                start=start,
                end=end,
            )

        # Mark the portfolio at the current open.
        marks.append(
            {
                "timestamp": timestamp,
                "raw_position": position,
                **portfolio.mark(price),
            }
        )

        # Candle `position` probability becomes known only at its close,
        # which is after the current open mark.
        if benchmark is None:
            pending = _consider_signal(
                position,
                origins=origins,
                market=market,
                portfolio=portfolio,
                policy=policy,
                config=config,
                model=model,
                trade_id=len(trades) + 1,
                end=end,
                counters=counters,
            )

    if portfolio.position is not None:
        raise AssertionError(
            "Incomplete positions must be excluded before entry"
        )

    curve = _build_equity_curve(
        marks,
        config,
    )

    trade_frame = pd.DataFrame(
        trades,
        columns=TRADE_COLUMNS,
    )

    diagnostics = _build_diagnostics(
        counters=counters,
        origins=origins,
        market_length=len(market),
        holding_period=config.holding_period,
        start=start,
        end=end,
    )

    return Simulation(
        equity=curve,
        trades=trade_frame,
        diagnostics=diagnostics,
    )