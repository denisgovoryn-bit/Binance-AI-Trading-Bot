"""Bybit V5 WebSocket stream construction and normalization."""

import asyncio
from collections.abc import AsyncIterator
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

from .rest import INTERVALS, _timeframe_ms


class BybitWebSocket:
    def __init__(self, market: MarketType, connector: Connector = default_connector) -> None:
        self.market = market
        self.connector = connector
        category = "spot" if market is MarketType.SPOT else "linear"
        self.url = f"wss://stream.bybit.com/v5/public/{category}"
        self._streams: list[ReconnectingJSONStream] = []

    def _stream(self, topic: str) -> ReconnectingJSONStream:
        stream = ReconnectingJSONStream(
            self.url,
            subscribe_message={"op": "subscribe", "args": [topic]},
            exchange="BYBIT",
            market=self.market.value,
            topics=(topic,),
            heartbeat_message={"op": "ping"},
            connector=self.connector,
        )
        self._streams.append(stream)
        return stream

    async def ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        state: dict[str, Any] = {}
        if self.market is MarketType.SPOT:
            stream = ReconnectingJSONStream(
                self.url,
                subscribe_message={
                    "op": "subscribe",
                    "args": [f"tickers.{symbol}", f"orderbook.1.{symbol}"],
                },
            )
            self._streams.append(stream)
        else:
            stream = self._stream(f"tickers.{symbol}")

        async for item in stream.messages():
            if "data" not in item:
                continue
            if item.get("topic", "").startswith("orderbook."):
                book = item["data"]
                if book.get("b"):
                    state["bid1Price"] = book["b"][0][0]
                if book.get("a"):
                    state["ask1Price"] = book["a"][0][0]
            else:
                state.update(item["data"])
            required = {
                "symbol",
                "lastPrice",
                "bid1Price",
                "ask1Price",
                "volume24h",
                "turnover24h",
            }
            if not required.issubset(state):
                continue
            yield Ticker(
                exchange=ExchangeName.BYBIT,
                market=self.market,
                symbol=state["symbol"],
                last_price=state["lastPrice"],
                bid_price=state["bid1Price"],
                ask_price=state["ask1Price"],
                volume_24h=state["volume24h"],
                quote_volume_24h=state["turnover24h"],
                timestamp=utc_from_milliseconds(item["ts"]),
            )

    async def trades(self, symbol: str) -> AsyncIterator[Trade]:
        async for item in self._stream(f"publicTrade.{symbol}").messages():
            for data in item.get("data", []):
                yield Trade(
                    exchange=ExchangeName.BYBIT,
                    market=self.market,
                    symbol=data["s"],
                    trade_id=data["i"],
                    price=data["p"],
                    quantity=data["v"],
                    buyer_is_maker=data["S"] == "Sell",
                    timestamp=utc_from_milliseconds(data["T"]),
                )

    async def orderbook(self, symbol: str) -> AsyncIterator[OrderBook]:
        async for book in self.orderbooks((symbol,)):
            yield book

    async def orderbooks(self, symbols: tuple[str, ...]) -> AsyncIterator[OrderBook]:
        topics = tuple(f"orderbook.50.{symbol}" for symbol in symbols)
        stream = ReconnectingJSONStream(
            self.url,
            subscribe_message={"op": "subscribe", "args": list(topics)},
            exchange="BYBIT",
            market=self.market.value,
            topics=topics,
            heartbeat_message={"op": "ping"},
            connector=self.connector,
        )
        self._streams.append(stream)
        books: dict[str, tuple[dict[str, str], dict[str, str]]] = {}
        async for item in stream.messages():
            if "data" not in item:
                continue
            data = item["data"]
            symbol = data["s"]
            if item.get("type") == "snapshot" or symbol not in books:
                bids = {row[0]: row[1] for row in data.get("b", []) if row[1] != "0"}
                asks = {row[0]: row[1] for row in data.get("a", []) if row[1] != "0"}
            else:
                bids, asks = books[symbol]
                self._apply_delta(bids, data.get("b", []))
                self._apply_delta(asks, data.get("a", []))
            books[symbol] = (bids, asks)
            if not bids or not asks:
                continue
            stream.mark_market_event()
            yield OrderBook(
                exchange=ExchangeName.BYBIT,
                market=self.market,
                symbol=symbol,
                bids=tuple(
                    OrderBookLevel(price=price, quantity=quantity)
                    for price, quantity in sorted(
                        bids.items(), key=lambda row: float(row[0]), reverse=True
                    )
                ),
                asks=tuple(
                    OrderBookLevel(price=price, quantity=quantity)
                    for price, quantity in sorted(asks.items(), key=lambda row: float(row[0]))
                ),
                timestamp=utc_from_milliseconds(item["ts"]),
                sequence=data.get("u"),
            )

    @staticmethod
    def _apply_delta(side: dict[str, str], updates: list[list[str]]) -> None:
        for price, quantity in updates:
            if quantity == "0":
                side.pop(price, None)
            else:
                side[price] = quantity

    async def candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        async for item in self._stream(f"kline.{INTERVALS[timeframe]}.{symbol}").messages():
            for data in item.get("data", []):
                start = int(data["start"])
                yield Candle(
                    exchange=ExchangeName.BYBIT,
                    market=self.market,
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=utc_from_milliseconds(start),
                    close_time=utc_from_milliseconds(start + _timeframe_ms(timeframe) - 1),
                    open=data["open"],
                    high=data["high"],
                    low=data["low"],
                    close=data["close"],
                    volume=data["volume"],
                    quote_volume=data["turnover"],
                    closed=data["confirm"],
                )

    async def close(self) -> None:
        await asyncio.gather(*(stream.close() for stream in self._streams))

    @property
    def reconnect_count(self) -> int:
        return sum(stream.reconnect_count for stream in self._streams)
