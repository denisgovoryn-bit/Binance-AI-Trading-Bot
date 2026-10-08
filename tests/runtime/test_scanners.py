import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.db.session import Database
from app.market.analysis import AnalysisService
from app.market.models import Candle, ExchangeName, MarketType, Timeframe
from app.runtime.scanners import ScannerRuntime
from app.telegram.bot import create_dispatcher


@pytest.mark.asyncio
async def test_runtime_scanners_start_and_stop_cleanly(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        symbol_universe_refresh_minutes=15,
        directional_full_scan_minutes=15,
    )
    database = Database(settings)
    await database.create_schema()

    async def sender(_chat_id: int, _text: str) -> None:
        return None

    runtime = ScannerRuntime(settings, database, {}, sender)
    snapshot = await runtime.start()
    with pytest.raises(RuntimeError, match="already started"):
        await runtime.start()
    assert snapshot.entries == ()
    assert runtime.state.universe_status == "RUNNING"
    assert runtime.state.directional_status == "RUNNING"
    assert runtime.state.arbitrage_status == "IDLE"
    directional = next(task for task in runtime._tasks if task.get_name() == "directional")
    directional.cancel()
    await asyncio.gather(directional, return_exceptions=True)
    await runtime.watchdog()
    assert any(task.get_name() == "directional" and not task.done() for task in runtime._tasks)
    dispatcher = create_dispatcher(database, settings, scanner_state=runtime.state)
    assert len(dispatcher.sub_routers) == 1
    await runtime.stop()
    assert runtime.state.directional_status == "STOPPED"
    assert runtime.state.arbitrage_status == "STOPPED"
    await database.close()


@pytest.mark.asyncio
async def test_runtime_analysis_excludes_developing_candle() -> None:
    intervals = {
        Timeframe.M15: timedelta(minutes=15),
        Timeframe.H1: timedelta(hours=1),
        Timeframe.H4: timedelta(hours=4),
    }

    class Adapter:
        async def get_candles(self, symbol, timeframe, limit):
            interval = intervals[timeframe]
            now = datetime.now(UTC)
            start = now - interval * 220
            rows = []
            for index in range(220):
                opened = start + interval * index
                price = Decimal("100") + Decimal(index) / Decimal("100")
                rows.append(
                    Candle(
                        exchange=ExchangeName.BINANCE,
                        market=MarketType.SPOT,
                        symbol=symbol,
                        timeframe=timeframe,
                        open_time=opened,
                        close_time=opened + interval - timedelta(milliseconds=1),
                        open=price,
                        high=price + 1,
                        low=price - 1,
                        close=price,
                        volume=Decimal("100"),
                    )
                )
            rows.append(
                rows[-1].model_copy(
                    update={
                        "open_time": now,
                        "close_time": now + interval,
                        "close": Decimal("999999"),
                    }
                )
            )
            return rows

    provider = ScannerRuntime._analysis_provider(
        Adapter(),
        AnalysisService(),  # type: ignore[arg-type]
    )
    result = await provider("BTCUSDT")
    assert result.timestamp <= datetime.now(UTC)
    assert result.timeframes[Timeframe.M15].current_price < 1_000
