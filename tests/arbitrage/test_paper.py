from decimal import Decimal

import pytest

from app.arbitrage.paper import paper_notional, two_leg_pnl

D = Decimal


def test_conservative_allocation_and_invalid_equity() -> None:
    assert paper_notional(D("1000"), D("25"), D("10")) == D("100")
    with pytest.raises(ValueError):
        paper_notional(D("0"), D("10"), D("10"))


def test_two_leg_convergence_fees_and_funding() -> None:
    result = two_leg_pnl(
        buy_entry=D("100"),
        buy_exit=D("100.2"),
        sell_entry=D("100.5"),
        sell_exit=D("100.2"),
        quantity=D("5"),
        fees=D("1"),
        funding_pnl=D("0.25"),
    )
    assert result.buy_leg_pnl == D("1.0")
    assert result.sell_leg_pnl == D("1.5")
    assert result.gross_pnl == D("2.5")
    assert result.net_pnl == D("1.75")


def test_two_leg_divergence_loss_and_funding_cost() -> None:
    result = two_leg_pnl(
        buy_entry=D("100"),
        buy_exit=D("99"),
        sell_entry=D("100.5"),
        sell_exit=D("101"),
        quantity=D("2"),
        fees=D("0.5"),
        funding_pnl=D("-0.2"),
    )
    assert result.net_pnl == D("-3.7")
