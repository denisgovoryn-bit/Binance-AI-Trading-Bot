"""Binance WebSocket stream construction and normalization."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.exchanges.websocket import Connector, ReconnectingJSONStream, default_connector
from app.market.models import (
    Candle,
    ExchangeName,
    MarketType,
    OrderBook,
    OrderBookLevel,
    Ticker,
    Timeframe,
    Trade,
    utc_from_milliseconds,
)


class BinanceWebSocket:
    def __init__(self, market: MarketType, connector: Connector = default_connector) -> None:
        self.market = market
        self.connector = connector
        self.base_url = (
            "wss://stream.binance.com:9443/ws"
            if market is MarketType.SPOT
            else "wss://fstream.binance.com/ws"
        )
        self._streams: list[ReconnectingJSONStream] = []

    def _stream(self, channel: str) -> ReconnectingJSONStream:
        stream = ReconnectingJSONStream(
            f"{self.base_url}/{channel}",
            exchange="BINANCE",
            market=self.market.value,
            topics=(channel,),
            connector=self.connector,
        )
        self._streams.append(stream)
        return stream

    async def ticker(self, symbol: str, initial: Ticker | None = None) -> AsyncIterator[Ticker]:
        if self.market is MarketType.FUTURES:
            stream = ReconnectingJSONStream(
                "wss://fstream.binance.com/ws",
                subscribe_message={
                    "method": "SUBSCRIBE",
                    "params": [
                        f"{symbol.lower()}@aggTrade",
                        f"{symbol.lower()}@bookTicker",
                    ],
                    "id": 1,
                },
            )
            self._streams.append(stream)
            state: dict[str, Any] = {}
            if initial is not None:
                state.update(
                    {
                        "s": initial.symbol,
                        "c": initial.last_price,
                        "b": initial.bid_price,
                        "a": initial.ask_price,
                        "v": initial.volume_24h,
                        "q": initial.quote_volume_24h,
                        "E": int(initial.timestamp.timestamp() * 1000),
                    }
                )
            messages = stream.messages()
        else:
            state = {}
            messages = self._stream(f"{symbol.lower()}@ticker").messages()

        async for message in messages:
            item = message.get("data", message)
            if item.get("e") == "aggTrade":
                item = {**item, "c": item["p"]}
            state.update(item)
            required = {"s", "c", "b", "a", "v", "q", "E"}
            if not required.issubset(state):
                continue
            yield Ticker(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=state["s"],
                last_price=state["c"],
                bid_price=state["b"],
                ask_price=state["a"],
                volume_24h=state["v"],
                quote_volume_24h=state["q"],
                timestamp=utc_from_milliseconds(state["E"]),
            )

    async def trades(self, symbol: str) -> AsyncIterator[Trade]:
        async for item in self._stream(f"{symbol.lower()}@trade").messages():
            yield Trade(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=item["s"],
                trade_id=str(item["t"]),
                price=item["p"],
                quantity=item["q"],
                buyer_is_maker=item["m"],
                timestamp=utc_from_milliseconds(item["T"]),
            )

    async def orderbook(self, symbol: str) -> AsyncIterator[OrderBook]:
        async for book in self.orderbooks((symbol,)):
            yield book

    async def orderbooks(self, symbols: tuple[str, ...]) -> AsyncIterator[OrderBook]:
        channels = tuple(f"{symbol.lower()}@depth20@100ms" for symbol in symbols)
        host = (
            "wss://stream.binance.com:9443/stream?streams="
            if self.market is MarketType.SPOT
            else "wss://fstream.binance.com/stream?streams="
        )
        stream = ReconnectingJSONStream(
            host + "/".join(channels),
            exchange="BINANCE",
            market=self.market.value,
            topics=channels,
            connector=self.connector,
        )
        self._streams.append(stream)
        async for message in stream.messages():
            item = message.get("data", message)
            bids = item.get("b", item.get("bids"))
            asks = item.get("a", item.get("asks"))
            if not isinstance(bids, list) or not isinstance(asks, list):
                continue
            channel = str(message.get("stream", ""))
            event_timestamp = utc_from_milliseconds(item["E"]) if "E" in item else datetime.now(UTC)
            stream.mark_market_event()
            yield OrderBook(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=str(item.get("s") or channel.partition("@")[0]).upper(),
                bids=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in bids),
                asks=tuple(OrderBookLevel(price=row[0], quantity=row[1]) for row in asks),
                timestamp=event_timestamp,
                sequence=item.get("u", item.get("lastUpdateId")),
            )

    async def candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        async for item in self._stream(f"{symbol.lower()}@kline_{timeframe.value}").messages():
            kline: dict[str, Any] = item["k"]
            yield Candle(
                exchange=ExchangeName.BINANCE,
                market=self.market,
                symbol=item["s"],
                timeframe=timeframe,
                open_time=utc_from_milliseconds(kline["t"]),
                close_time=utc_from_milliseconds(kline["T"]),
                open=kline["o"],
                high=kline["h"],
                low=kline["l"],
                close=kline["c"],
                volume=kline["v"],
                quote_volume=kline["q"],
                closed=kline["x"],
            )

    async def close(self) -> None:
        await asyncio.gather(*(stream.close() for stream in self._streams))

    @property
    def reconnect_count(self) -> int:
        return sum(stream.reconnect_count for stream in self._streams)
