"""Live public-data universe, one directional cycle, and bounded arbitrage smoke test."""

import asyncio

from app.arbitrage.realtime import RealtimeArbitrageManager
from app.arbitrage.scanner import ArbitrageCandidate, ArbitrageScanner
from app.config.settings import Settings
from app.db.session import Database
from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.market.models import ExchangeName, MarketType
from app.runtime.scanners import ScannerRuntime


async def main() -> None:
    settings = Settings()
    database = Database(settings)
    exchanges: dict[tuple[ExchangeName, MarketType], BaseExchange] = {
        (ExchangeName.BINANCE, MarketType.SPOT): BinanceSpotExchange(),
        (ExchangeName.BINANCE, MarketType.FUTURES): BinanceFuturesExchange(),
        (ExchangeName.BYBIT, MarketType.SPOT): BybitSpotExchange(),
        (ExchangeName.BYBIT, MarketType.FUTURES): BybitFuturesExchange(),
    }

    async def no_delivery(_chat_id: int, _text: str) -> None:
        return None

    runtime = ScannerRuntime(settings, database, exchanges, no_delivery)
    try:
        await database.create_schema()
        snapshot = await runtime.universe.refresh()
        for exchange in ExchangeName:
            for market in MarketType:
                count = len(snapshot.symbols(exchange, market))
                print(f"{exchange.title()} {market.title()} eligible: {count}")
        print(f"Directional final universe: {len(snapshot.directional)}")
        print(f"Cross-exchange Spot arbitrage universe: {len(snapshot.cross_exchange_spot)}")
        print(f"Cross-exchange Futures arbitrage universe: {len(snapshot.cross_exchange_futures)}")
        print(f"Basis combinations: {len(snapshot.basis)}")
        print(f"Funding comparison universe: {len(snapshot.funding)}")

        await runtime.scan_once()
        print(
            "Directional: "
            f"actionable={runtime.state.latest_actionable} "
            f"pending={runtime.state.latest_pending} "
            f"watchlist={runtime.state.latest_watchlist} "
            f"NO_TRADE={runtime.state.latest_no_trade} "
            f"duration_ms={runtime.state.last_directional_duration_ms:.2f}"
        )

        candidates: list[ArbitrageCandidate] = []

        async def collect(candidate: ArbitrageCandidate) -> None:
            candidates.append(candidate)

        scanner = ArbitrageScanner(settings)
        realtime = RealtimeArbitrageManager(scanner, collect)
        smoke_limit = min(3, settings.arbitrage_runtime_max_symbols_per_market)
        for market, symbols in (
            (MarketType.SPOT, snapshot.cross_exchange_spot[:smoke_limit]),
            (MarketType.FUTURES, snapshot.cross_exchange_futures[:smoke_limit]),
        ):
            for exchange in ExchangeName:
                realtime.subscribe(exchanges[(exchange, market)], symbols)
        await asyncio.sleep(10)
        await realtime.close()
        print(
            f"Arbitrage: events={realtime.events_processed} "
            f"candidates={len(candidates)} qualified={scanner.metrics.qualified}"
        )
        print("Public read-only validation only. Trading execution: DISABLED")
    finally:
        for adapter in exchanges.values():
            await adapter.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
