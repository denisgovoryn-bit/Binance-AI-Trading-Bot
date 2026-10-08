from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.market.models import ExchangeName, MarketType, SymbolInfo, Ticker
from app.universe.service import SymbolUniverseService

NOW = datetime.now(UTC)


class FakeExchange:
    def __init__(
        self,
        exchange: ExchangeName,
        market: MarketType,
        rows: list[tuple[str, str, str, bool, str, str]],
        *,
        fail: bool = False,
        ticker_fail: bool = False,
    ) -> None:
        self.exchange, self.market, self.rows = exchange, market, rows
        self.fail, self.ticker_fail = fail, ticker_fail

    async def get_symbols(self) -> list[SymbolInfo]:
        if self.fail:
            raise ConnectionError
        return [
            SymbolInfo(
                exchange=self.exchange,
                market=self.market,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                active=active,
                listed_at=NOW - timedelta(days=30),
            )
            for symbol, base, quote, active, _, _ in self.rows
        ]

    async def get_tickers(self) -> list[Ticker]:
        if self.fail or self.ticker_fail:
            raise ConnectionError
        return [
            Ticker(
                exchange=self.exchange,
                market=self.market,
                symbol=symbol,
                last_price=100,
                bid_price=Decimal("100") - Decimal(spread) / 2,
                ask_price=Decimal("100") + Decimal(spread) / 2,
                volume_24h=1,
                quote_volume_24h=volume,
                timestamp=NOW,
            )
            for symbol, _, _, _, volume, spread in self.rows
        ]


def row(
    symbol: str,
    volume: str = "20000000",
    spread: str = "0.02",
    *,
    active: bool = True,
    quote: str = "USDT",
) -> tuple[str, str, str, bool, str, str]:
    return symbol, symbol.removesuffix(quote), quote, active, volume, spread


def service(
    settings: Settings, mapping: dict[tuple[ExchangeName, MarketType], FakeExchange]
) -> SymbolUniverseService:
    return SymbolUniverseService(settings, mapping)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_filtering_ranking_core_and_max_size() -> None:
    settings = Settings(
        symbol_universe_max_per_market=5,
        symbol_universe_min_24h_quote_volume=10_000_000,
        symbol_universe_max_spread_pct=0.2,
        symbol_universe_blacklist=("BADUSDT",),
    )
    rows = [
        row("ETHUSDT", "12000000"),
        row("BTCUSDT", "11000000"),
        row("HIGHUSDT", "90000000"),
        row("LOWUSDT", "100"),
        row("WIDEUSDT", spread="1"),
        row("BADUSDT"),
        row("OLDUPUSDT"),
        row("USDCUSDT"),
        row("OFFUSDT", active=False),
        row("BTCUSD", quote="USD"),
    ]
    snapshot = await service(
        settings,
        {
            (ExchangeName.BINANCE, MarketType.SPOT): FakeExchange(
                ExchangeName.BINANCE, MarketType.SPOT, rows
            )
        },
    ).refresh()
    assert [item.symbol for item in snapshot.entries] == ["BTCUSDT", "ETHUSDT", "HIGHUSDT"]


@pytest.mark.asyncio
async def test_whitelist_and_cross_market_projections_deduplicate_directional() -> None:
    settings = Settings(
        symbol_universe_whitelist=("BTCUSDT", "ETHUSDT"),
        symbol_universe_min_24h_quote_volume=0,
    )
    mapping = {
        (exchange, market): FakeExchange(exchange, market, [row("BTCUSDT"), row("ETHUSDT")])
        for exchange in ExchangeName
        for market in MarketType
    }
    snapshot = await service(settings, mapping).refresh()
    assert snapshot.cross_exchange_spot == ("BTCUSDT", "ETHUSDT")
    assert snapshot.cross_exchange_futures == ("BTCUSDT", "ETHUSDT")
    assert snapshot.funding == snapshot.cross_exchange_futures
    assert len(snapshot.basis) == 4
    assert len(snapshot.directional) == 4


@pytest.mark.asyncio
async def test_exchange_failure_isolated_and_last_snapshot_retained() -> None:
    settings = Settings(symbol_universe_min_24h_quote_volume=0)
    good = FakeExchange(ExchangeName.BINANCE, MarketType.SPOT, [row("BTCUSDT")])
    failed = FakeExchange(ExchangeName.BYBIT, MarketType.SPOT, [], fail=True)
    universe = service(
        settings,
        {
            (ExchangeName.BINANCE, MarketType.SPOT): good,
            (ExchangeName.BYBIT, MarketType.SPOT): failed,
        },
    )
    first = await universe.refresh()
    assert first.symbols(ExchangeName.BINANCE, MarketType.SPOT) == ("BTCUSDT",)
    assert first.degraded_markets == ("BYBIT_SPOT",)
    good.fail = True
    assert await universe.refresh() == first


@pytest.mark.asyncio
async def test_core_fallback_uses_only_supported_active_symbols() -> None:
    settings = Settings(symbol_universe_min_24h_quote_volume=0)
    adapter = FakeExchange(
        ExchangeName.BINANCE,
        MarketType.SPOT,
        [row("BTCUSDT"), row("ETHUSDT"), row("SOLUSDT", active=False), row("RANDOMUSDT")],
        ticker_fail=True,
    )
    snapshot = await service(settings, {(ExchangeName.BINANCE, MarketType.SPOT): adapter}).refresh()
    assert snapshot.used_fallback
    assert [item.symbol for item in snapshot.entries] == ["BTCUSDT", "ETHUSDT"]
