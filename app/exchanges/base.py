"""Abstract exchange market-data contract."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.market.models import (
    Candle,
    FundingRate,
    OpenInterest,
    OrderBook,
    SymbolInfo,
    Ticker,
    Timeframe,
    Trade,
)


class BaseExchange(ABC):
    """Unified public market-data interface consumed by scanners and replay engines."""

    @abstractmethod
    async def get_symbols(self) -> list[SymbolInfo]: ...

    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker: ...

    @abstractmethod
    async def get_tickers(self) -> list[Ticker]: ...

    @abstractmethod
    async def get_candles(
        self, symbol: str, timeframe: Timeframe, limit: int = 200
    ) -> list[Candle]: ...

    @abstractmethod
    async def get_orderbook(self, symbol: str, limit: int = 50) -> OrderBook: ...

    @abstractmethod
    async def get_recent_trades(self, symbol: str, limit: int = 100) -> list[Trade]: ...

    @abstractmethod
    async def get_funding_rate(self, symbol: str) -> FundingRate | None: ...

    @abstractmethod
    async def get_open_interest(self, symbol: str) -> OpenInterest | None: ...

    @abstractmethod
    def subscribe_ticker(self, symbol: str) -> AsyncIterator[Ticker]: ...

    @abstractmethod
    def subscribe_trades(self, symbol: str) -> AsyncIterator[Trade]: ...

    @abstractmethod
    def subscribe_orderbook(self, symbol: str) -> AsyncIterator[OrderBook]: ...

    async def subscribe_orderbooks(self, symbols: tuple[str, ...]) -> AsyncIterator[OrderBook]:
        """Batch book stream; adapters may override to share one physical socket."""
        if len(symbols) != 1:
            raise NotImplementedError("adapter does not support batched order books")
        async for book in self.subscribe_orderbook(symbols[0]):
            yield book

    @abstractmethod
    def subscribe_candles(self, symbol: str, timeframe: Timeframe) -> AsyncIterator[Candle]: ...

    @abstractmethod
    async def close(self) -> None: ...
