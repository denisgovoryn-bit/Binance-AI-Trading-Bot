import asyncio
from datetime import UTC, datetime

import pytest

from app.arbitrage.realtime import RealtimeArbitrageManager
from app.market.models import ExchangeName, MarketType, OrderBook, OrderBookLevel


def book(sequence: int) -> OrderBook:
    return OrderBook(
        exchange=ExchangeName.BINANCE,
        market=MarketType.SPOT,
        symbol="BTCUSDT",
        bids=(OrderBookLevel(price="100", quantity="1"),),
        asks=(OrderBookLevel(price="101", quantity="1"),),
        timestamp=datetime.now(UTC),
        sequence=sequence,
    )


class Exchange:
    async def subscribe_orderbooks(self, _symbols: tuple[str, ...]):
        yield book(1)
        yield book(2)


class Scanner:
    def update(self, _book, *, now):
        return [object()]


@pytest.mark.asyncio
async def test_candidate_handler_failure_does_not_stop_market_freshness() -> None:
    handled = 0

    async def handler(_candidate) -> None:
        nonlocal handled
        handled += 1
        if handled == 1:
            raise RuntimeError("downstream persistence failed")

    manager = RealtimeArbitrageManager(Scanner(), handler)  # type: ignore[arg-type]
    await manager._consume(Exchange(), ("BTCUSDT",))  # type: ignore[arg-type]
    assert manager.events_processed == 2
    assert manager.errors == 1
    assert manager.last_event_at is not None
    assert manager.last_event_by_exchange[ExchangeName.BINANCE] is not None


@pytest.mark.asyncio
async def test_candidate_persistence_is_serialized_across_consumers() -> None:
    active = 0
    maximum_active = 0

    async def handler(_candidate) -> None:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1

    manager = RealtimeArbitrageManager(Scanner(), handler)  # type: ignore[arg-type]
    await asyncio.gather(
        manager._consume(Exchange(), ("BTCUSDT",)),  # type: ignore[arg-type]
        manager._consume(Exchange(), ("BTCUSDT",)),  # type: ignore[arg-type]
    )
    assert maximum_active == 1
