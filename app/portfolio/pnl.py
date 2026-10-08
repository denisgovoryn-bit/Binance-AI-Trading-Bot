"""Decimal P&L and equity calculations for BUY/LONG/SHORT paper positions."""

from decimal import Decimal


def gross_pnl(direction: str, entry: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    difference = exit_price - entry
    if direction == "SHORT":
        difference = -difference
    return (difference * quantity).quantize(Decimal("0.00000001"))


def unrealized_pnl(direction: str, entry: Decimal, current: Decimal, quantity: Decimal) -> Decimal:
    return gross_pnl(direction, entry, current, quantity)


def return_percentage(net_pnl: Decimal, notional: Decimal) -> Decimal:
    if notional == 0:
        return Decimal("0")
    return (net_pnl / notional * Decimal("100")).quantize(Decimal("0.0001"))
