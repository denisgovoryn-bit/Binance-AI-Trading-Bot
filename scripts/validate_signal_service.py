"""Two-cycle public-data validation for Phase 3B; never accesses accounts or orders."""

import asyncio
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.main import create_app
from app.market.analysis import AnalysisService, MarketAnalysis
from app.market.models import Candle, Timeframe
from app.signals.ranking import group_top
from app.signals.service import SignalService

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT")
TIMEFRAMES = (Timeframe.M15, Timeframe.H1, Timeframe.H4)


def completed(candles: list[Candle], now: datetime) -> list[Candle]:
    return [candle for candle in candles if candle.close_time <= now]


async def analyze_symbol(adapter: BaseExchange, symbol: str, now: datetime) -> MarketAnalysis:
    batches = await asyncio.gather(
        *(adapter.get_candles(symbol, timeframe, 300) for timeframe in TIMEFRAMES)
    )
    candles = {
        timeframe: completed(batch, now)
        for timeframe, batch in zip(TIMEFRAMES, batches, strict=True)
    }
    return AnalysisService().analyze_market(candles)


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="signal-service-") as directory:
        settings = Settings(database_url=f"sqlite+aiosqlite:///{Path(directory) / 'live.db'}")
        database = Database(settings)
        await database.create_schema()
        adapters: list[BaseExchange] = [
            BinanceSpotExchange(),
            BinanceFuturesExchange(),
            BybitSpotExchange(),
            BybitFuturesExchange(),
        ]
        analyses = []
        now = datetime.now(UTC)
        try:
            for adapter in adapters:
                for symbol in SYMBOLS:
                    try:
                        analyses.append(await analyze_symbol(adapter, symbol, now))
                    except Exception as exc:
                        print(f"ERROR {type(adapter).__name__} {symbol}: {exc}")
        finally:
            await asyncio.gather(*(adapter.close() for adapter in adapters))
        async with database.sessions() as session:
            repo = SignalRepository(session)
            service = SignalService(repo, settings)

            async def cycle() -> dict[str, int]:
                outcomes = []
                contexts = {(x.exchange, x.market): x for x in analyses if x.symbol == "BTCUSDT"}
                for analysis in analyses:
                    outcomes.append(
                        await service.evaluate(
                            analysis,
                            btc_context=contexts.get((analysis.exchange, analysis.market)),
                            evaluated_at=now,
                        )
                    )
                actions = Counter(item.action for item in outcomes)
                classes = Counter(item.signal.classification.value for item in outcomes)
                return (
                    dict(actions)
                    | dict(classes)
                    | {
                        "analyses": len(outcomes),
                        "actionable": classes["STRONG_SETUP"] + classes["VERY_STRONG_SETUP"],
                    }
                )

            started = time.perf_counter()
            first, second = await cycle(), await cycle()
            elapsed = (time.perf_counter() - started) * 1000
            rows = await repo.list_signals(statuses=["WAITING_ENTRY"], limit=100)
            top = group_top(rows)
            print(f"First scan: {first}")
            print(f"Second scan: {second}")
            print(f"Top signals ({len(top)}):")
            for item in top:
                row = item["signal"]
                print(
                    f"  {row.exchange} {row.market} {row.symbol} {row.direction} "
                    f"confidence={row.current_confidence:.2f} "
                    f"alternatives={len(item['alternative_venues'])}"
                )
            print(
                f"Persistence/dedup/ranking benchmark: {elapsed:.2f} ms for "
                f"{len(analyses) * 2} evaluations"
            )
            persisted = len(await repo.list_signals(limit=100))
            print(f"Persistence validation: persisted={persisted}, top={len(top)}")
        await database.close()
        with TestClient(create_app(settings)) as client:
            listed = client.get("/signals")
            api_top = client.get("/signals/top")
            print(
                f"API validation: list_status={listed.status_code} "
                f"list_count={listed.json()['count']} top_status={api_top.status_code} "
                f"top_count={api_top.json()['count']}"
            )


if __name__ == "__main__":
    asyncio.run(main())
