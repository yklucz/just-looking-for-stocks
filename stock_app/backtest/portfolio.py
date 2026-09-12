"""Single position with fractional adjusted units and cash-funded commissions."""
from dataclasses import dataclass, field

from ..config import BacktestConfig
from .costs import commission, effective_price


@dataclass
class Portfolio:
    config: BacktestConfig
    cash: float = field(init=False)
    position: dict | None = None
    realized_pnl: float = 0.0
    commissions: float = 0.0
    slippage_cost: float = 0.0
    turnover_notional: float = 0.0

    def __post_init__(self) -> None:
        self.cash = self.config.initial_capital

    def enter(self, raw_price: float, details: dict) -> None:
        if self.position is not None:
            raise ValueError("Overlapping positions forbidden")
        effective = effective_price(raw_price, "entry", self.config)
        budget = self.cash * self.config.position_size
        quantity = budget / (effective * (1 + self.config.commission_bps / 10000))
        fee = commission(quantity * effective, self.config)
        self.cash -= quantity * effective + fee
        if self.cash < -1e-8:
            raise ValueError("Negative cash; leverage is disabled")
        self.cash = max(self.cash, 0.0)
        self.position = {**details, "quantity": quantity, "raw_entry_price": raw_price,
                         "effective_entry_price": effective, "entry_commission": fee,
                         "position_value": quantity * raw_price, "entry_outlay": budget}
        self.commissions += fee
        self.slippage_cost += quantity * (effective - raw_price)
        self.turnover_notional += quantity * raw_price

    def exit(self, raw_price: float, timestamp, raw_position: int) -> dict:
        if self.position is None:
            raise ValueError("No position to exit")
        p = self.position
        effective = effective_price(raw_price, "exit", self.config)
        quantity = p["quantity"]
        exit_fee = commission(quantity * effective, self.config)
        self.cash += quantity * effective - exit_fee
        fees = p["entry_commission"] + exit_fee
        slippage = quantity * (p["effective_entry_price"] - p["raw_entry_price"] + raw_price - effective)
        gross = quantity * (raw_price - p["raw_entry_price"])
        net = gross - fees - slippage
        trade = {**p, "exit_timestamp": timestamp, "exit_position": raw_position,
                 "raw_exit_price": raw_price, "effective_exit_price": effective,
                 "exit_commission": exit_fee, "commission": fees, "slippage_cost": slippage,
                 "gross_pnl": gross, "net_pnl": net,
                 "gross_return": raw_price / p["raw_entry_price"] - 1,
                 "net_return": net / p["entry_outlay"], "return_pct": 100 * net / p["entry_outlay"],
                 "holding_bars": raw_position - p["entry_position"]}
        self.realized_pnl += net
        self.commissions += exit_fee
        self.slippage_cost += quantity * (raw_price - effective)
        self.turnover_notional += quantity * raw_price
        self.position = None
        return trade

    def mark(self, raw_price: float) -> dict:
        p = self.position
        units = p["quantity"] if p else 0.0
        value = units * raw_price
        equity = self.cash + value
        unrealized = value - p["entry_outlay"] if p else 0.0
        return {"equity": equity, "cash": self.cash, "quantity": units,
                "position_value": value, "exposure": value / equity if equity else 0.0,
                "invested": int(p is not None), "realized_pnl": self.realized_pnl,
                "unrealized_pnl": unrealized, "commission": self.commissions,
                "slippage_cost": self.slippage_cost, "turnover_notional": self.turnover_notional}
