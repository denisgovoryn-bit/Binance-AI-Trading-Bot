"""Benchmark Phase 2 and print read-only public BTCUSDT analysis snapshots."""

import argparse
import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.market.analysis import AnalysisService, TimeframeAnalysis
from app.market.models import Candle, ExchangeName, MarketType, Timeframe
from app.market.structure import ZoneKind


def benchmark(count: int = 5_000) -> float:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(count):
        drift = Decimal(index) / Decimal(100)
        cycle = Decimal((index % 40) - 20) / Decimal(10)
        close = Decimal(50_000) + drift + cycle
        candles.append(
            Candle(
                exchange=ExchangeName.BINANCE,
                market=MarketType.SPOT,
                symbol="BTCUSDT",
                timeframe=Timeframe.M1,
                open_time=start + timedelta(minutes=index),
                close_time=start + timedelta(minutes=index, seconds=59),
                open=close - Decimal("0.2"),
                high=close + Decimal("2"),
                low=close - Decimal("2"),
                close=close,
                volume=Decimal(100 + index % 25),
            )
        )
    started = time.perf_counter()
    AnalysisService().analyze_timeframe(candles)
    return (time.perf_counter() - started) * 1_000


async def analyze_adapter(adapter: BaseExchange) -> tuple[str, TimeframeAnalysis]:
    try:
        candles = await adapter.get_candles("BTCUSDT", Timeframe.M15, 500)
        return type(adapter).__name__, AnalysisService().analyze_timeframe(candles)
    finally:
        await adapter.close()


def nearest_zone(analysis: TimeframeAnalysis, kind: ZoneKind) -> str:
    reference = analysis.current_price
    candidates = [zone for zone in analysis.zones if zone.kind is kind and not zone.broken]
    if not candidates:
        return "none"
    zone = min(candidates, key=lambda item: abs(item.midpoint - reference))
    return f"{zone.lower:.2f}-{zone.upper:.2f} ({zone.touches} touches)"


async def live_snapshots() -> None:
    adapters: list[BaseExchange] = [
        BinanceSpotExchange(),
        BinanceFuturesExchange(),
        BybitSpotExchange(),
        BybitFuturesExchange(),
    ]
    results = await asyncio.gather(
        *(analyze_adapter(adapter) for adapter in adapters), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            print(f"ERROR: {type(result).__name__}: {result}")
            continue
        name, analysis = result
        print(
            f"{name}: trend={analysis.trend.state.value}, "
            f"ATR%={analysis.indicators.atr.percentage.current:.3f}, "
            f"RSI={analysis.indicators.rsi.current:.2f}, "
            f"volume={analysis.volume.state.value}, "
            f"structure={analysis.structure.state.value}, "
            f"support={nearest_zone(analysis, ZoneKind.SUPPORT)}, "
            f"resistance={nearest_zone(analysis, ZoneKind.RESISTANCE)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()
    print(f"5000-candle analysis: {benchmark():.2f} ms")
    if not args.benchmark_only:
        asyncio.run(live_snapshots())


if __name__ == "__main__":
    main()
