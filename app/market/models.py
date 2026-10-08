"""Exchange-neutral market data models."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class ExchangeName(StrEnum):
    BINANCE = "BINANCE"
    BYBIT = "BYBIT"


class MarketType(StrEnum):
    SPOT = "SPOT"
    FUTURES = "FUTURES"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"


class SymbolInfo(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    base_asset: str
    quote_asset: str
    active: bool
    price_tick: Decimal | None = None
    quantity_step: Decimal | None = None
    listed_at: datetime | None = None


class Ticker(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    last_price: Decimal
    bid_price: Decimal
    ask_price: Decimal
    volume_24h: Decimal
    quote_volume_24h: Decimal
    timestamp: datetime

    @property
    def spread_bps(self) -> Decimal:
        midpoint = (self.ask_price + self.bid_price) / 2
        return (
            Decimal(0) if midpoint == 0 else (self.ask_price - self.bid_price) / midpoint * 10_000
        )


class Candle(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal | None = None
    closed: bool = True

    @model_validator(mode="after")
    def validate_prices(self) -> "Candle":
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle OHLC values are inconsistent")
        return self


class OrderBookLevel(BaseModel):
    price: Decimal
    quantity: Decimal = Field(ge=0)


class OrderBook(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    bids: tuple[OrderBookLevel, ...]
    asks: tuple[OrderBookLevel, ...]
    timestamp: datetime
    sequence: int | None = None


class Trade(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    trade_id: str
    price: Decimal
    quantity: Decimal = Field(gt=0)
    buyer_is_maker: bool
    timestamp: datetime


class FundingRate(BaseModel):
    exchange: ExchangeName
    symbol: str
    rate: Decimal
    funding_time: datetime


class OpenInterest(BaseModel):
    exchange: ExchangeName
    symbol: str
    value: Decimal = Field(ge=0)
    timestamp: datetime


def utc_from_milliseconds(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
