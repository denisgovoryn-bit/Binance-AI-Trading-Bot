"""Read-only public-data validation and Phase 3A evaluation benchmark."""

import asyncio
import time
from collections.abc import Iterable
from datetime import UTC, datetime

from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.market.analysis import AnalysisService, MarketAnalysis
from app.market.models import Candle, ExchangeName, MarketType, Timeframe
from app.signals.engine import DirectionalSignalEngine
from app.signals.models import DirectionalSignal, SignalClassification

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
TIMEFRAMES = (Timeframe.M15, Timeframe.H1, Timeframe.H4)


def completed(candles: list[Candle], now: datetime) -> list[Candle]:
    """Exclude the exchange's currently developing REST candle."""

    return [candle for candle in candles if candle.close_time <= now]


async def analyze_symbol(adapter: BaseExchange, symbol: str, now: datetime) -> MarketAnalysis:
    batches = await asyncio.gather(
        *(adapter.get_candles(symbol, timeframe, 300) for timeframe in TIMEFRAMES)
    )
    by_timeframe = {
        timeframe: completed(candles, now)
        for timeframe, candles in zip(TIMEFRAMES, batches, strict=True)
    }
    return AnalysisService().analyze_market(by_timeframe)


async def analyze_adapter(adapter: BaseExchange, now: datetime) -> list[MarketAnalysis | Exception]:
    try:
        results: list[MarketAnalysis | Exception] = []
        for symbol in SYMBOLS:
            try:
                results.append(await analyze_symbol(adapter, symbol, now))
                print(f"Analyzed {type(adapter).__name__} {symbol}", flush=True)
            except Exception as exc:
                results.append(exc)
                print(f"Failed {type(adapter).__name__} {symbol}: {exc}", flush=True)
        return results
    finally:
        await adapter.close()


def print_signal(signal: DirectionalSignal) -> None:
    heading = (
        f"{signal.exchange.value} {signal.market_type.value} {signal.symbol}: "
        f"{signal.classification.value}"
    )
    if signal.classification in {
        SignalClassification.STRONG_SETUP,
        SignalClassification.VERY_STRONG_SETUP,
    }:
        assert signal.entry_zone is not None
        assert signal.invalidation is not None
        assert signal.direction is not None
        current = signal.supporting_metrics.get("current_price")
        entry_state = signal.supporting_metrics.get("entry_zone_state", "PENDING_ENTRY")
        distance = signal.supporting_metrics.get("distance_to_entry_zone_atr")
        assert isinstance(current, (int, float))
        assert isinstance(distance, (int, float))
        print(
            f"{heading} {signal.direction.value} score={signal.confidence_score:.2f} "
            f"current={float(current):.4f} "
            f"entry={signal.entry_zone.low:.4f}-{signal.entry_zone.high:.4f} "
            f"stop={signal.invalidation.price:.4f} "
            f"qualification={entry_state} distance_atr={float(distance):.2f}"
        )
        for target in signal.targets:
            print(f"  {target.name}={target.price:.4f} ({target.r_multiple:.2f}R)")
        print(f"  reasons: {'; '.join(signal.reasons[:6])}")
        if signal.warnings:
            print(f"  warnings: {'; '.join(signal.warnings[:4])}")
    else:
        explanation = signal.rejection_reasons or signal.warnings or signal.reasons
        print(f"{heading} score={signal.confidence_score:.2f} — {'; '.join(explanation[:3])}")


def benchmark(
    analyses: Iterable[MarketAnalysis],
    btc_contexts: dict[tuple[ExchangeName, MarketType], MarketAnalysis],
    now: datetime,
    repetitions: int = 25,
) -> tuple[int, float]:
    snapshots = list(analyses)
    engine = DirectionalSignalEngine()
    started = time.perf_counter()
    count = 0
    for _ in range(repetitions):
        for market in snapshots:
            context = btc_contexts.get((market.exchange, market.market))
            engine.evaluate(market, btc_context=context, evaluated_at=now)
            count += 1
    elapsed_ms = (time.perf_counter() - started) * 1_000
    return count, elapsed_ms


async def main() -> None:
    now = datetime.now(UTC)
    adapters: list[BaseExchange] = [
        BinanceSpotExchange(),
        BinanceFuturesExchange(),
        BybitSpotExchange(),
        BybitFuturesExchange(),
    ]
    batches = await asyncio.gather(*(analyze_adapter(adapter, now) for adapter in adapters))
    analyses: list[MarketAnalysis] = []
    for batch in batches:
        for result in batch:
            if isinstance(result, Exception):
                print(f"ANALYSIS ERROR: {type(result).__name__}: {result}")
            else:
                analyses.append(result)
    btc_contexts = {
        (market.exchange, market.market): market
        for market in analyses
        if market.symbol == "BTCUSDT"
    }
    engine = DirectionalSignalEngine()
    for market in analyses:
        print_signal(
            engine.evaluate(
                market,
                btc_context=btc_contexts.get((market.exchange, market.market)),
                evaluated_at=now,
            )
        )
    count, elapsed_ms = benchmark(analyses, btc_contexts, now)
    print(
        f"Benchmark: {count} precomputed MarketAnalysis evaluations in {elapsed_ms:.2f} ms "
        f"({elapsed_ms / max(count, 1):.3f} ms/evaluation)"
    )


if __name__ == "__main__":
    asyncio.run(main())
