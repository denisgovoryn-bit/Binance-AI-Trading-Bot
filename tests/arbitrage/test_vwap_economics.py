from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.arbitrage.engine import calculate_cross_book
from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.models import BookLevel, BookSnapshot
from app.arbitrage.vwap import buy_vwap, sell_vwap
from app.config.settings import Settings
from app.market.models import ExchangeName, MarketType

NOW = datetime(2026, 8, 9, tzinfo=UTC)
D = Decimal


def levels(*values: tuple[str, str]) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(price=D(price), quantity=D(quantity)) for price, quantity in values)


def book(exchange: ExchangeName, bid: str, ask: str, *, bid_qty="20", ask_qty="20") -> BookSnapshot:
    return BookSnapshot(
        exchange=exchange,
        market=MarketType.SPOT,
        symbol="BTCUSDT",
        bids=levels((bid, bid_qty)),
        asks=levels((ask, ask_qty)),
        exchange_timestamp=NOW,
        received_at=NOW,
    )


def test_single_and_multi_level_vwap_decimal_precision() -> None:
    single = buy_vwap(levels(("100", "5")), D("500"))
    assert single.vwap == D("100") and single.quantity == D("5")
    multi = buy_vwap(levels(("100", "2"), ("102", "4")), D("500"))
    expected_quantity = D("2") + D("300") / D("102")
    assert multi.vwap == D("500") / expected_quantity
    assert multi.levels_consumed == 2 and multi.slippage_pct > 0
    sold = sell_vwap(levels(("101", "2"), ("99", "4")), D("5"), D("500"))
    assert sold.vwap == D("499") / D("5") and sold.levels_consumed == 2


def test_insufficient_depth_partial_execution_and_invalid_amounts() -> None:
    with pytest.raises(ValueError, match="insufficient"):
        buy_vwap(levels(("100", "1")), D("500"))
    partial = buy_vwap(levels(("100", "1")), D("500"), allow_partial=True)
    assert not partial.complete and partial.executable_notional == D("100")
    assert partial.liquidity_ratio == D("0.2")
    for amount in (D("0"), D("-1")):
        with pytest.raises(ValueError):
            buy_vwap(levels(("100", "1")), amount)


def test_malformed_unsorted_and_crossed_books_rejected() -> None:
    with pytest.raises(ValidationError):
        BookSnapshot(
            exchange=ExchangeName.BINANCE,
            market=MarketType.SPOT,
            symbol="BTCUSDT",
            bids=levels(("99", "1"), ("100", "1")),
            asks=levels(("101", "1")),
            exchange_timestamp=NOW,
            received_at=NOW,
        )
    with pytest.raises(ValidationError):
        book(ExchangeName.BINANCE, "101", "101")
    with pytest.raises(ValidationError):
        BookLevel(price=D("0"), quantity=D("1"))


def test_positive_edge_and_cost_elimination() -> None:
    settings = Settings(arbitrage_safety_buffer_bps=2)
    buy = book(ExchangeName.BINANCE, "99.9", "100")
    sell = book(ExchangeName.BYBIT, "100.50", "100.60")
    result = calculate_cross_book(buy, sell, D("500"), ArbitrageFeeProvider(settings), settings)
    assert result.gross_spread_pct == D("0.500")
    assert result.fees_pct == D("0.20")
    assert result.safety_buffer_pct == D("0.02")
    assert result.expected_net_edge_pct == D("0.280")
    assert result.expected_net_pnl == D("1.400")
    eliminated = calculate_cross_book(
        book(ExchangeName.BINANCE, "99", "100"),
        book(ExchangeName.BYBIT, "100.1", "100.2"),
        D("500"),
        ArbitrageFeeProvider(settings),
        settings,
    )
    assert eliminated.expected_net_edge_pct < 0
