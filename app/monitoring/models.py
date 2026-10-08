"""Normalized monitoring inputs and outputs."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class PriceSource(StrEnum):
    TICKER = "TICKER"
    TRADE = "TRADE"
    CANDLE = "CANDLE"
    RECOVERY_SNAPSHOT = "RECOVERY_SNAPSHOT"


class PriceEvent(BaseModel):
    symbol: str
    last: float = Field(gt=0)
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    exchange_timestamp: datetime
    received_at: datetime
    source: PriceSource = PriceSource.TICKER
    sequence: int | None = None

    @model_validator(mode="after")
    def validate_quote(self) -> "PriceEvent":
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        return self

    @property
    def data_age_ms(self) -> float:
        return max(0.0, (self.received_at - self.exchange_timestamp).total_seconds() * 1000)


class CandlePriceEvent(BaseModel):
    symbol: str
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    exchange_timestamp: datetime
    received_at: datetime


class MonitorResult(BaseModel):
    signal_id: str
    previous_status: str
    current_status: str
    events: tuple[str, ...] = ()
    ignored_reason: str | None = None
