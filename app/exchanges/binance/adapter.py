"""Unified Binance adapter implementation."""

from collections.abc import AsyncIterator

from app.exchanges.base import BaseExchange
from app.exchanges.binance.rest import BinanceREST
from app.exchanges.binance.websocket import BinanceWebSocket
from app.market.models import (
    Candle,
    FundingRate,
    MarketType,
    OpenInterest,
    OrderBook,
    SymbolInfo,
    Ticker,
    Timeframe,
    Trade,
)


class BinanceExchange(BaseExchange):
    def __init__(
        self,
        market: MarketType,
        rest: BinanceREST | None = None,
        websocket: BinanceWebSocket | None = None,
    ) -> None:
        self.market = market
        self.rest = rest or BinanceREST(market)
        self.websocket = websocket or BinanceWebSocket(market)

    async def get_symbols(self) -> list[SymbolInfo]:
        return await self.rest.get_symbols()

    async def get_ticker(self, symbol: str) -> Ticker:
        return await self.rest.get_ticker(symbol)

    async def get_tickers(self) -> list[Ticker]:
        return await self.rest.get_tickers()

    async def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 200
    ) -> list[Candle]:
        return await self.rest.get_candles(symbol, timeframe, limit)

    async def get_orderbook(self, symbol: str, limit: int = 50) -> OrderBook:
        return await self.rest.get_orderbook(symbol, limit)

    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[Trade]:
        return await self.rest.get_recent_trades(symbol, limit)

    async def get_funding_rate(self, symbol: str) -> FundingRate | None:
        return await self.rest.get_funding_rate(symbol)

    async def get_open_interest(self, symbol: str) -> OpenInterest | None:
        return await self.rest.get_open_interest(symbol)

    def subscribe_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        if self.market is MarketType.FUTURES:
            return self._subscribe_futures_ticker(symbol)
        return self.websocket.ticker(symbol)

    async def _subscribe_futures_ticker(self, symbol: str) -> AsyncIterator[Ticker]:
        initial = await self.rest.get_ticker(symbol)
        async for ticker in self.websocket.ticker(symbol, initial=initial):
            yield ticker

    def subscribe_trades(self, symbol: str) -> AsyncIterator[Trade]:
        return self.websocket.trades(symbol)

    def subscribe_orderbook(self, symbol: str) -> AsyncIterator[OrderBook]:
        return self.websocket.orderbook(symbol)

    def subscribe_orderbooks(self, symbols: tuple[str, ...]) -> AsyncIterator[OrderBook]:
        return self.websocket.orderbooks(symbols)

    def subscribe_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]:
        return self.websocket.candles(symbol, timeframe)

    async def close(self) -> None:
        await self.rest.close()
        await self.websocket.close()
