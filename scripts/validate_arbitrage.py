"""Public-market arbitrage smoke validation and deterministic performance benchmark."""

import asyncio
import statistics
import time
from datetime import UTC, datetime
from decimal import Decimal

from app.arbitrage.basis import evaluate_basis
from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.funding import evaluate_funding
from app.arbitrage.liquidity import normalize_book
from app.arbitrage.models import BookLevel, BookSnapshot
from app.arbitrage.scanner import ArbitrageScanner
from app.arbitrage.vwap import buy_vwap
from app.config.settings import Settings
from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.market.models import ExchangeName, MarketType

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")


async def live() -> None:
    settings = Settings()
    scanner = ArbitrageScanner(settings)
    adapters: dict[tuple[ExchangeName, MarketType], BaseExchange] = {
        (ExchangeName.BINANCE, MarketType.SPOT): BinanceSpotExchange(),
        (ExchangeName.BINANCE, MarketType.FUTURES): BinanceFuturesExchange(),
        (ExchangeName.BYBIT, MarketType.SPOT): BybitSpotExchange(),
        (ExchangeName.BYBIT, MarketType.FUTURES): BybitFuturesExchange(),
    }
    books = {}
    best_edge: Decimal | None = None
    basis_count = funding_count = 0
    websocket_events = 0
    started = time.perf_counter()
    try:
        requests = [
            (
                key,
                symbol,
                asyncio.create_task(asyncio.wait_for(adapter.get_orderbook(symbol, 50), 10)),
            )
            for symbol in SYMBOLS
            for key, adapter in adapters.items()
        ]
        results = await asyncio.gather(*(item[2] for item in requests), return_exceptions=True)
        for (key, symbol, _), raw in zip(requests, results, strict=True):
            if isinstance(raw, BaseException):
                scanner.metrics.errors += 1
                print(f"ERROR {key[0]} {key[1]} {symbol}: {type(raw).__name__}")
                continue
            snapshot = normalize_book(raw, received_at=datetime.now(UTC))
            books[(*key, symbol)] = snapshot
            for candidate in scanner.update(snapshot):
                edge = candidate.economics.expected_net_edge_pct
                best_edge = edge if best_edge is None else max(best_edge, edge)
        fees = ArbitrageFeeProvider(settings)
        for exchange in ExchangeName:
            for symbol in SYMBOLS:
                spot = books.get((exchange, MarketType.SPOT, symbol))
                futures = books.get((exchange, MarketType.FUTURES, symbol))
                if spot and futures:
                    evaluate_basis(spot, futures, Decimal("100"), fees, settings)
                    basis_count += 1
        for symbol in SYMBOLS:
            first, second = (
                adapters[(ExchangeName.BINANCE, MarketType.FUTURES)],
                adapters[(ExchangeName.BYBIT, MarketType.FUTURES)],
            )
            rates = await asyncio.gather(
                asyncio.wait_for(first.get_funding_rate(symbol), 10),
                asyncio.wait_for(second.get_funding_rate(symbol), 10),
                return_exceptions=True,
            )
            if all(rate is not None and not isinstance(rate, BaseException) for rate in rates):
                evaluate_funding(rates[0], rates[1], Decimal("0.21"), settings)  # type: ignore[arg-type]
                funding_count += 1
        streams = (
            adapters[(ExchangeName.BINANCE, MarketType.SPOT)].subscribe_orderbook("BTCUSDT"),
            adapters[(ExchangeName.BYBIT, MarketType.SPOT)].subscribe_orderbook("BTCUSDT"),
        )
        websocket_books = await asyncio.gather(
            *(asyncio.wait_for(anext(stream), 15) for stream in streams), return_exceptions=True
        )
        for raw in websocket_books:
            if not isinstance(raw, BaseException):
                scanner.update(normalize_book(raw, received_at=datetime.now(UTC)))
                websocket_events += 1
    finally:
        await asyncio.gather(*(adapter.close() for adapter in adapters.values()))
    elapsed = (time.perf_counter() - started) * 1000
    m = scanner.metrics
    print(f"Symbols: {', '.join(SYMBOLS)}")
    print(
        f"Book events={m.updates} evaluations={m.evaluations} "
        f"gross={m.gross_candidates} qualified={m.qualified}"
    )
    print(
        f"fee_edge_rejected={m.fee_edge_rejections} "
        f"liquidity_rejected={m.liquidity_rejections} stale_rejected={m.stale_rejections}"
    )
    print(
        f"basis={basis_count} funding={funding_count} websocket_events={websocket_events} "
        f"best_net_edge={best_edge} latency_ms={elapsed:.2f}"
    )
    print("Public data only. No orders or private APIs were used.")


def benchmark() -> None:
    levels = tuple(
        BookLevel(price=Decimal("100") + Decimal(i) / 100, quantity=Decimal("2")) for i in range(20)
    )
    samples = []
    started = time.perf_counter()
    for _ in range(10_000):
        tick = time.perf_counter_ns()
        buy_vwap(levels, Decimal("500"))
        samples.append((time.perf_counter_ns() - tick) / 1_000)
    total = (time.perf_counter() - started) * 1000
    samples.sort()
    print(
        f"10,000 VWAP: total={total:.2f}ms avg={statistics.mean(samples):.3f}us "
        f"p95={samples[9499]:.3f}us"
    )
    now = datetime.now(UTC)
    settings = Settings(
        arbitrage_target_notionals=(100.0,),
        arbitrage_min_net_edge_bps=1,
    )
    scanner = ArbitrageScanner(settings)
    binance = BookSnapshot(
        exchange=ExchangeName.BINANCE,
        market=MarketType.SPOT,
        symbol="BTCUSDT",
        bids=(BookLevel(price=Decimal("99.9"), quantity=Decimal("100")),),
        asks=(BookLevel(price=Decimal("100"), quantity=Decimal("100")),),
        exchange_timestamp=now,
        received_at=now,
    )
    bybit = BookSnapshot(
        exchange=ExchangeName.BYBIT,
        market=MarketType.SPOT,
        symbol="BTCUSDT",
        bids=(BookLevel(price=Decimal("100.5"), quantity=Decimal("100")),),
        asks=(BookLevel(price=Decimal("100.6"), quantity=Decimal("100")),),
        exchange_timestamp=now,
        received_at=now,
    )
    scanner.update(binance, now=now)
    comparison_samples = []
    for _ in range(10_000):
        tick = time.perf_counter_ns()
        scanner.update(bybit, now=now)
        comparison_samples.append((time.perf_counter_ns() - tick) / 1_000)
    comparison_samples.sort()
    print(
        f"10,000 comparisons: avg={statistics.mean(comparison_samples):.3f}us "
        f"p95={comparison_samples[9499]:.3f}us"
    )


async def main() -> None:
    benchmark()
    await live()


if __name__ == "__main__":
    asyncio.run(main())
