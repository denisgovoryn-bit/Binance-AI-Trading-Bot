#!/usr/bin/env python3
"""Sanitized one-symbol public WebSocket connectivity diagnostic."""

import asyncio
from datetime import UTC, datetime
from time import perf_counter

from app.exchanges.binance.websocket import BinanceWebSocket
from app.exchanges.bybit.websocket import BybitWebSocket
from app.market.models import MarketType


async def validate(name: str, websocket: BinanceWebSocket | BybitWebSocket) -> None:
    started = perf_counter()
    events = 0
    last_event = None
    finished = None
    error: str | None = None
    try:
        try:
            async with asyncio.timeout(30):
                async for _book in websocket.orderbook("BTCUSDT"):
                    events += 1
                    last_event = datetime.now(UTC)
                    if events >= 10:
                        finished = datetime.now(UTC)
                        break
        except TimeoutError:
            error = "bounded validation timeout"
    finally:
        await websocket.close()
    duration = perf_counter() - started
    age = ((finished or datetime.now(UTC)) - last_event).total_seconds() if last_event else None
    result = (
        f"{name}: events={events} duration={duration:.2f}s "
        f"reconnects={websocket.reconnect_count} last_event_age={age:.3f}s"
        if age is not None
        else f"{name}: events=0 duration={duration:.2f}s reconnects={websocket.reconnect_count}"
    )
    print(f"{result} error={error}" if error else result)


async def main() -> None:
    checks = (
        ("Binance Spot", BinanceWebSocket(MarketType.SPOT)),
        ("Binance Futures", BinanceWebSocket(MarketType.FUTURES)),
        ("Bybit Spot", BybitWebSocket(MarketType.SPOT)),
        ("Bybit Linear Futures", BybitWebSocket(MarketType.FUTURES)),
    )
    for name, websocket in checks:
        await validate(name, websocket)


if __name__ == "__main__":
    asyncio.run(main())
