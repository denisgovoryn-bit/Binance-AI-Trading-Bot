import json

import pytest

from app.exchanges.websocket import ReconnectingJSONStream


class FakeConnection:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True

    async def _iterate(self):
        for message in self.messages:
            yield json.dumps(message)

    def __aiter__(self):
        return self._iterate()


@pytest.mark.asyncio
async def test_stream_reconnects_and_resubscribes_after_disconnect() -> None:
    connections = [FakeConnection([]), FakeConnection([{"price": "10"}])]
    calls = 0

    async def connect(url: str) -> FakeConnection:
        nonlocal calls
        connection = connections[calls]
        calls += 1
        return connection

    stream = ReconnectingJSONStream(
        "wss://example.test",
        subscribe_message={"op": "subscribe", "args": ["ticker.BTCUSDT"]},
        connector=connect,  # type: ignore[arg-type]
        base_delay=0,
    )

    message = await anext(stream.messages())
    await stream.close()

    assert message == {"price": "10"}
    assert calls == 2
    assert stream.reconnect_count == 1
    assert connections[0].sent == connections[1].sent
    assert connections[1].closed
