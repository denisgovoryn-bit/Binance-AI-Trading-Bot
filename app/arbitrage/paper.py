"""Decimal two-leg paper arbitrage accounting."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ArbitragePaperResult:
    buy_leg_pnl: Decimal
    sell_leg_pnl: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding_pnl: Decimal
    net_pnl: Decimal


def paper_notional(equity: Decimal, user_max_pct: Decimal, system_max_pct: Decimal) -> Decimal:
    if equity <= 0:
        raise ValueError("paper equity must be positive")
    allocation = min(user_max_pct, system_max_pct)
    if allocation <= 0 or allocation > 100:
        raise ValueError("paper allocation must be within (0, 100]")
    return equity * allocation / Decimal("100")


def two_leg_pnl(
    *,
    buy_entry: Decimal,
    buy_exit: Decimal,
    sell_entry: Decimal,
    sell_exit: Decimal,
    quantity: Decimal,
    fees: Decimal,
    funding_pnl: Decimal = Decimal("0"),
) -> ArbitragePaperResult:
    if min(buy_entry, buy_exit, sell_entry, sell_exit, quantity) <= 0 or fees < 0:
        raise ValueError("prices/quantity must be positive and fees nonnegative")
    buy_result = (buy_exit - buy_entry) * quantity
    sell_result = (sell_entry - sell_exit) * quantity
    gross = buy_result + sell_result
    return ArbitragePaperResult(
        buy_leg_pnl=buy_result,
        sell_leg_pnl=sell_result,
        gross_pnl=gross,
        fees=fees,
        funding_pnl=funding_pnl,
        net_pnl=gross - fees + funding_pnl,
    )
