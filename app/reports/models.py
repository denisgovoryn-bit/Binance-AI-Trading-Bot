"""Typed paper performance report output."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class PerformanceMetrics(BaseModel):
    starting_equity: Decimal
    ending_equity: Decimal
    net_pnl: Decimal
    return_pct: Decimal
    fees: Decimal
    open_positions: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    average_winner: Decimal
    average_loser: Decimal
    expectancy: Decimal
    average_r: Decimal
    max_drawdown: Decimal
    current_drawdown: Decimal
    best_trade: Decimal | None
    worst_trade: Decimal | None
    average_holding_seconds: Decimal
    segments: dict[str, dict[str, dict[str, Decimal | int | None]]]


class PerformanceReport(BaseModel):
    report_type: str
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    account_id: int | None
    global_strategy: bool
    metrics: PerformanceMetrics
