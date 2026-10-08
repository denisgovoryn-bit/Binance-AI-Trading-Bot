from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.arbitrage.models import BookLevel, BookSnapshot
from app.arbitrage.scanner import ArbitrageScanner
from app.config.settings import Settings
from app.market.models import ExchangeName, MarketType

NOW = datetime(2026, 8, 9, tzinfo=UTC)
D = Decimal


def make_book(
    exchange: ExchangeName,
    bid: str,
    ask: str,
    *,
    market: MarketType = MarketType.SPOT,
    timestamp: datetime = NOW,
    quantity: str = "100",
    recovered: bool = True,
) -> BookSnapshot:
    return BookSnapshot(
        exchange=exchange,
        market=market,
        symbol="BTCUSDT",
        bids=(BookLevel(price=D(bid), quantity=D(quantity)),),
        asks=(BookLevel(price=D(ask), quantity=D(quantity)),),
        exchange_timestamp=timestamp,
        received_at=NOW,
        recovery_complete=recovered,
    )


def settings(**kwargs: object) -> Settings:
    values: dict[str, object] = {
        "arbitrage_target_notionals": (100.0,),
        "arbitrage_min_net_edge_bps": 1,
        "arbitrage_max_quote_age_ms": 1000,
        "arbitrage_max_cross_exchange_skew_ms": 500,
    }
    values.update(kwargs)
    return Settings(**values)  # type: ignore[arg-type]


def test_scans_both_cross_exchange_directions_and_markets() -> None:
    scanner = ArbitrageScanner(settings())
    assert scanner.update(make_book(ExchangeName.BINANCE, "99.9", "100"), now=NOW) == []
    results = scanner.update(make_book(ExchangeName.BYBIT, "100.5", "100.6"), now=NOW)
    assert [(item.buy_exchange, item.sell_exchange) for item in results] == [
        (ExchangeName.BINANCE, ExchangeName.BYBIT)
    ]
    futures = ArbitrageScanner(settings())
    futures.update(
        make_book(ExchangeName.BINANCE, "100.5", "100.6", market=MarketType.FUTURES), now=NOW
    )
    reverse = futures.update(
        make_book(ExchangeName.BYBIT, "99.9", "100", market=MarketType.FUTURES), now=NOW
    )
    assert reverse[0].buy_exchange is ExchangeName.BYBIT
    assert reverse[0].sell_exchange is ExchangeName.BINANCE


def test_stale_skew_and_recovery_books_are_rejected() -> None:
    stale = ArbitrageScanner(settings())
    stale.update(make_book(ExchangeName.BINANCE, "99", "100"), now=NOW)
    assert (
        stale.update(make_book(ExchangeName.BYBIT, "101", "102"), now=NOW + timedelta(seconds=2))
        == []
    )
    assert stale.metrics.stale_rejections == 1

    skewed = ArbitrageScanner(settings())
    skewed.update(make_book(ExchangeName.BINANCE, "99", "100"), now=NOW)
    assert (
        skewed.update(
            make_book(
                ExchangeName.BYBIT, "101", "102", timestamp=NOW + timedelta(milliseconds=600)
            ),
            now=NOW + timedelta(milliseconds=600),
        )
        == []
    )
    assert skewed.metrics.skew_rejections == 1

    recovery = ArbitrageScanner(settings())
    recovery.update(make_book(ExchangeName.BINANCE, "99", "100", recovered=False), now=NOW)
    assert recovery.update(make_book(ExchangeName.BYBIT, "101", "102"), now=NOW) == []


def test_depth_slippage_and_fee_filters_are_counted() -> None:
    shallow = ArbitrageScanner(settings())
    shallow.update(make_book(ExchangeName.BINANCE, "99", "100", quantity="0.1"), now=NOW)
    assert shallow.update(make_book(ExchangeName.BYBIT, "110", "111"), now=NOW) == []
    assert shallow.metrics.liquidity_rejections == 2

    fees = ArbitrageScanner(settings(arbitrage_min_net_edge_bps=100))
    fees.update(make_book(ExchangeName.BINANCE, "99", "100"), now=NOW)
    assert fees.update(make_book(ExchangeName.BYBIT, "100.5", "101"), now=NOW) == []
    assert fees.metrics.fee_edge_rejections == 1
