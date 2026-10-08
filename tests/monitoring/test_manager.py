import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.exchanges.base import BaseExchange
from app.market.models import (
    Candle,
    ExchangeName,
    FundingRate,
    MarketType,
    OpenInterest,
    OrderBook,
    SymbolInfo,
    Ticker,
    Timeframe,
    Trade,
)
from app.monitoring.manager import MonitoringManager
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


class ReconnectingFakeExchange(BaseExchange):
    def __init__(self) -> None:
        self.stream_starts = 0

    async def get_ticker(self, symbol: str) -> Ticker:
        return self._ticker(symbol)

    async def get_tickers(self) -> list[Ticker]:
        return []

    async def get_symbols(self) -> list[SymbolInfo]:
        return []

    async def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 200
    ) -> list[Candle]:
        return []

    async def get_orderbook(self, symbol: str, limit: int = 50) -> OrderBook:
        raise NotImplementedError

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[Trade]:
        return []

    async def get_funding_rate(self, symbol: str) -> FundingRate | None:
        return None

    async def get_open_interest(self, symbol: str) -> OpenInterest | None:
        return None

    def subscribe_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        return self._stream(symbol)

    def subscribe_trades(self, symbol: str) -> AsyncIterator[Trade]:
        return self._empty()

    def subscribe_orderbook(self, symbol: str) -> AsyncIterator[OrderBook]:
        return self._empty()

    def subscribe_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        return self._empty()

    async def close(self) -> None:
        pass

    async def _stream(self, symbol: str) -> AsyncIterator[Ticker]:
        self.stream_starts += 1
        yield self._ticker(symbol)
        raise ConnectionError("fixture disconnect")

    async def _empty(self):
        if False:
            yield None

    @staticmethod
    def _ticker(symbol: str) -> Ticker:
        return Ticker(
            exchange=ExchangeName.BINANCE,
            market=MarketType.FUTURES,
            symbol=symbol,
            last_price=Decimal("100"),
            bid_price=Decimal("99.9"),
            ask_price=Decimal("100.1"),
            volume_24h=Decimal("1"),
            quote_volume_24h=Decimal("1"),
            timestamp=NOW,
        )


@pytest.mark.asyncio
async def test_manager_restores_subscription_and_reconciles_without_duplicates(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'manager.db'}",
        monitor_reconnect_initial_seconds=0.01,
        monitor_reconnect_max_seconds=0.01,
    )
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        repo = SignalRepository(session)
        signal = DirectionalSignalEngine().evaluate(make_market(bullish=True), evaluated_at=NOW)
        outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
        assert outcome.record is not None
        adapter = ReconnectingFakeExchange()
        manager = MonitoringManager(
            repo, settings, {(ExchangeName.BINANCE, MarketType.FUTURES): adapter}
        )
        await manager.sync_subscriptions()
        await asyncio.sleep(0.2)
        await manager.stop()
        assert adapter.stream_starts >= 2
        assert manager.reconnect_count >= 1
        assert [event.event_type for event in outcome.record.monitoring_events] == [
            "MONITORING_DEGRADED"
        ]
    await database.close()


@pytest.mark.asyncio
async def test_manager_observes_signal_created_by_another_session_after_startup(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'dynamic.db'}",
        monitor_reconnect_initial_seconds=10,
    )
    database = Database(settings)
    await database.create_schema()
    adapter = ReconnectingFakeExchange()
    async with database.sessions() as monitor_session:
        manager = MonitoringManager(
            SignalRepository(monitor_session),
            settings,
            {(ExchangeName.BINANCE, MarketType.FUTURES): adapter},
        )
        await manager.sync_subscriptions()
        assert manager.monitored_signal_count == 0
        async with database.sessions() as writer_session:
            signal = DirectionalSignalEngine().evaluate(make_market(bullish=True), evaluated_at=NOW)
            outcome = await SignalService(SignalRepository(writer_session), settings).process(
                signal, evaluated_at=NOW
            )
            assert outcome.record is not None
        await manager.sync_subscriptions()
        assert manager.monitored_signal_count == 1
        assert len(manager.tasks) == 1
        await manager.stop()
    await database.close()


@pytest.mark.asyncio
async def test_manager_restores_pending_signal_created_before_startup(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'restored.db'}",
        monitor_reconnect_initial_seconds=10,
    )
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as writer_session:
        signal = DirectionalSignalEngine().evaluate(make_market(bullish=True), evaluated_at=NOW)
        await SignalService(SignalRepository(writer_session), settings).process(
            signal, evaluated_at=NOW
        )
    async with database.sessions() as monitor_session:
        manager = MonitoringManager(
            SignalRepository(monitor_session),
            settings,
            {(ExchangeName.BINANCE, MarketType.FUTURES): ReconnectingFakeExchange()},
        )
        await manager.sync_subscriptions()
        assert manager.monitored_signal_count == 1
        assert len(manager.tasks) == 1
        await manager.stop()
    await database.close()
