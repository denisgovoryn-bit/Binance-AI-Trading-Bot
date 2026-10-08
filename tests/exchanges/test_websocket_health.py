import asyncio
import json
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest

from app.exchanges.binance.websocket import BinanceWebSocket
from app.exchanges.bybit.websocket import BybitWebSocket
from app.exchanges.websocket import ReconnectingJSONStream
from app.market.models import MarketType


class Connection:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        return None

    async def _messages(self) -> AsyncIterator[str]:
        for message in self.messages:
            yield json.dumps(message)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._messages()


@pytest.mark.asyncio
async def test_subscription_rejection_is_surfaced_and_backed_off(monkeypatch) -> None:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)
        raise asyncio.CancelledError

    async def connector(_url: str) -> Connection:
        return Connection(
            [{"op": "subscribe", "success": False, "retCode": 10001, "ret_msg": "bad topic"}]
        )

    monkeypatch.setattr(asyncio, "sleep", sleep)
    stream = ReconnectingJSONStream(
        "wss://example.test/public",
        subscribe_message={"op": "subscribe", "args": ["bad"]},
        connector=connector,  # type: ignore[arg-type]
        base_delay=1,
        topics=("bad",),
    )
    with pytest.raises(asyncio.CancelledError):
        await anext(stream.messages())
    assert stream.reconnect_count == 1
    assert delays and delays[0] > 0
    assert not stream.subscription_acknowledged


@pytest.mark.asyncio
async def test_backoff_resets_only_after_valid_market_event(monkeypatch) -> None:
    connections = [Connection([]), Connection([{"data": 1}]), Connection([])]
    calls = 0
    delays: list[float] = []

    async def connector(_url: str) -> Connection:
        nonlocal calls
        result = connections[calls]
        calls += 1
        return result

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr("app.exchanges.websocket.random.uniform", lambda _a, _b: 1.0)
    stream = ReconnectingJSONStream(
        "wss://example.test/public", connector=connector, base_delay=1, maximum_delay=8
    )
    messages = stream.messages()
    message = await anext(messages)
    assert message == {"data": 1}
    stream.mark_market_event()
    with pytest.raises(asyncio.CancelledError):
        await anext(messages)
    assert delays == [1, 1]


@pytest.mark.asyncio
async def test_consecutive_connection_failures_use_exponential_backoff(monkeypatch) -> None:
    delays: list[float] = []

    async def connector(_url: str) -> Connection:
        raise OSError("offline")

    async def sleep(delay: float) -> None:
        delays.append(delay)
        if len(delays) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", sleep)
    monkeypatch.setattr("app.exchanges.websocket.random.uniform", lambda _a, _b: 1.0)
    stream = ReconnectingJSONStream(
        "wss://example.test/public", connector=connector, base_delay=1, maximum_delay=8
    )
    with pytest.raises(asyncio.CancelledError):
        await anext(stream.messages())
    assert delays == [1, 2, 4]


@pytest.mark.asyncio
async def test_bybit_snapshot_and_delta_produce_complete_books() -> None:
    connection = Connection(
        [
            {"op": "subscribe", "success": True, "conn_id": "safe-id"},
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "snapshot",
                "ts": 1_700_000_000_000,
                "data": {
                    "s": "BTCUSDT",
                    "b": [["100", "2"], ["99", "1"]],
                    "a": [["101", "2"], ["102", "1"]],
                    "u": 1,
                },
            },
            {
                "topic": "orderbook.50.BTCUSDT",
                "type": "delta",
                "ts": 1_700_000_000_001,
                "data": {"s": "BTCUSDT", "b": [["100", "0"], ["100.5", "3"]], "a": [], "u": 2},
            },
        ]
    )

    async def connector(_url: str) -> Connection:
        return connection

    websocket = BybitWebSocket(MarketType.SPOT, connector=connector)  # type: ignore[arg-type]
    try:
        books = websocket.orderbooks(("BTCUSDT",))
        first = await anext(books)
        second = await anext(books)
    finally:
        await websocket.close()
    assert first.bids[0].price == Decimal("100")
    assert second.bids[0].price == Decimal("100.5")
    assert second.asks[0].price == Decimal("101")
    assert websocket._streams[0].subscription_acknowledged


@pytest.mark.asyncio
async def test_binance_spot_partial_depth_snapshot_is_market_event() -> None:
    connection = Connection(
        [
            {
                "stream": "btcusdt@depth20@100ms",
                "data": {
                    "lastUpdateId": 42,
                    "bids": [["100", "2"]],
                    "asks": [["101", "3"]],
                },
            }
        ]
    )

    async def connector(_url: str) -> Connection:
        return connection

    websocket = BinanceWebSocket(MarketType.SPOT, connector=connector)  # type: ignore[arg-type]
    try:
        book = await anext(websocket.orderbook("BTCUSDT"))
    finally:
        await websocket.close()
    assert book.symbol == "BTCUSDT"
    assert book.sequence == 42
    assert websocket._streams[0]._valid_event_since_connect
