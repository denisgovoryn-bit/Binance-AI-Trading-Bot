from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.arbitrage.basis import evaluate_basis
from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.funding import evaluate_funding
from app.arbitrage.models import BookLevel, BookSnapshot
from app.config.settings import Settings
from app.market.models import ExchangeName, FundingRate, MarketType

NOW = datetime(2026, 8, 9, tzinfo=UTC)
D = Decimal


def book(exchange: ExchangeName, market: MarketType, bid: str, ask: str) -> BookSnapshot:
    return BookSnapshot(
        exchange=exchange,
        market=market,
        symbol="BTCUSDT",
        bids=(BookLevel(price=D(bid), quantity=D("100")),),
        asks=(BookLevel(price=D(ask), quantity=D("100")),),
        exchange_timestamp=NOW,
        received_at=NOW,
    )


@pytest.mark.parametrize("exchange", [ExchangeName.BINANCE, ExchangeName.BYBIT])
def test_positive_and_reverse_same_exchange_basis(exchange: ExchangeName) -> None:
    settings = Settings()
    fees = ArbitrageFeeProvider(settings)
    positive = evaluate_basis(
        book(exchange, MarketType.SPOT, "99.9", "100"),
        book(exchange, MarketType.FUTURES, "101", "101.1"),
        D("500"),
        fees,
        settings,
    )
    assert positive.basis_pct > 0 and not positive.reverse
    assert positive.economics.expected_net_edge_pct > 0
    reverse = evaluate_basis(
        book(exchange, MarketType.SPOT, "101", "101.1"),
        book(exchange, MarketType.FUTURES, "99.9", "100"),
        D("500"),
        fees,
        settings,
    )
    assert reverse.reverse and "borrowing" in reverse.warnings[0]


def test_funding_direction_cost_recovery_and_warnings() -> None:
    settings = Settings(arbitrage_max_funding_time_skew_minutes=10)
    binance = FundingRate(
        exchange=ExchangeName.BINANCE, symbol="ETHUSDT", rate=D("0.0001"), funding_time=NOW
    )
    bybit = FundingRate(
        exchange=ExchangeName.BYBIT,
        symbol="ETHUSDT",
        rate=D("-0.0002"),
        funding_time=NOW + timedelta(minutes=20),
    )
    candidate = evaluate_funding(binance, bybit, D("0.05"), settings)
    assert candidate.long_exchange is ExchangeName.BYBIT
    assert candidate.short_exchange is ExchangeName.BINANCE
    assert candidate.next_interval_advantage_pct == D("0.03")
    assert candidate.break_even_intervals == 2
    assert len(candidate.warnings) == 2


def test_zero_differential_cannot_recover_costs() -> None:
    settings = Settings()
    one = FundingRate(
        exchange=ExchangeName.BINANCE, symbol="BTCUSDT", rate=D("0.0001"), funding_time=NOW
    )
    two = FundingRate(
        exchange=ExchangeName.BYBIT, symbol="BTCUSDT", rate=D("0.0001"), funding_time=NOW
    )
    result = evaluate_funding(one, two, D("0.20"), settings)
    assert result.break_even_intervals is None
    assert "does not offset" in result.warnings[-1]
