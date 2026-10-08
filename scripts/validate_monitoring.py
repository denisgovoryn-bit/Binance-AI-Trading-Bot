"""Deterministic replay/benchmark plus optional public-WebSocket smoke validation."""

import argparse
import asyncio
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.exchanges.binance.spot import BinanceSpotExchange
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


async def deterministic() -> None:
    with tempfile.TemporaryDirectory(prefix="monitor-replay-") as directory:
        settings = Settings(database_url=f"sqlite+aiosqlite:///{Path(directory) / 'replay.db'}")
        database = Database(settings)
        await database.create_schema()
        async with database.sessions() as session:
            repo, engine = SignalRepository(session), DirectionalSignalEngine()
            base = engine.evaluate(make_market(bullish=True), evaluated_at=NOW)
            records = []
            for index in range(100):
                signal = base.model_copy(update={"symbol": f"T{index:03d}USDT"})
                outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
                assert outcome.record is not None
                records.append((outcome.record, signal))
            monitor = SignalMonitor(repo, settings)
            started = time.perf_counter()
            for record, signal in records:
                price = signal.ideal_entry or 1
                await monitor.process_price(
                    record,
                    PriceEvent(
                        symbol=record.symbol,
                        last=price,
                        bid=price,
                        ask=price,
                        exchange_timestamp=NOW,
                        received_at=NOW,
                    ),
                )
            entry_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            for record, signal in records:
                await monitor.refresh_confidence(record, signal, NOW + timedelta(milliseconds=1))
            refresh_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            for record, signal in records:
                await monitor.process_price(
                    record,
                    PriceEvent(
                        symbol=record.symbol,
                        last=signal.targets[-1].price,
                        bid=signal.targets[-1].price,
                        ask=signal.targets[-1].price,
                        exchange_timestamp=NOW + timedelta(seconds=1),
                        received_at=NOW + timedelta(seconds=1),
                    ),
                )
            target_ms = (time.perf_counter() - started) * 1000
            first = records[0][0]
            print(
                f"Deterministic replay: {first.status}, "
                f"events={[event.event_type for event in first.monitoring_events]}"
            )
            print(f"100-signal entry throughput: {entry_ms:.2f}ms ({entry_ms / 100:.3f}ms/signal)")
            print(
                f"100-signal cached-analysis refresh persistence: {refresh_ms:.2f}ms "
                f"({refresh_ms / 100:.3f}ms/signal)"
            )
            print(
                f"100-signal target persistence: {target_ms:.2f}ms ({target_ms / 100:.3f}ms/signal)"
            )
        await database.close()


async def live_smoke() -> None:
    adapter = BinanceSpotExchange()
    try:
        ticker = await asyncio.wait_for(anext(adapter.subscribe_ticker("BTCUSDT")), timeout=20)
        age = (datetime.now(UTC) - ticker.timestamp).total_seconds() * 1000
        print(
            f"Live public WebSocket: {ticker.exchange.value} {ticker.market.value} "
            f"{ticker.symbol} last={ticker.last_price} age_ms={age:.1f}"
        )
        print(
            "No persisted WAITING_ENTRY signal selected; ingestion validated "
            "without forced trigger."
        )
    finally:
        await adapter.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    await deterministic()
    if args.live:
        await live_smoke()


if __name__ == "__main__":
    asyncio.run(main())
