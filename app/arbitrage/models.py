"""Typed exchange-independent arbitrage values."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from app.market.models import ExchangeName, MarketType


class ArbitrageType(StrEnum):
    CROSS_EXCHANGE_SPOT = "CROSS_EXCHANGE_SPOT"
    CROSS_EXCHANGE_FUTURES = "CROSS_EXCHANGE_FUTURES"
    SPOT_FUTURES_BASIS = "SPOT_FUTURES_BASIS"
    FUNDING_RATE = "FUNDING_RATE"


class ArbitrageStatus(StrEnum):
    DETECTED = "DETECTED"
    PERSISTING = "PERSISTING"
    QUALIFIED = "QUALIFIED"
    WEAKENING = "WEAKENING"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CLOSED = "CLOSED"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    PAPER_COMPLETED = "PAPER_COMPLETED"


class ArbitrageClassification(StrEnum):
    REJECTED = "REJECTED"
    WATCHLIST = "WATCHLIST"
    STRONG_OPPORTUNITY = "STRONG_OPPORTUNITY"
    VERY_STRONG_OPPORTUNITY = "VERY_STRONG_OPPORTUNITY"


class BookLevel(BaseModel):
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class BookSnapshot(BaseModel):
    exchange: ExchangeName
    market: MarketType
    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    exchange_timestamp: datetime
    received_at: datetime
    sequence: int | None = None
    recovery_complete: bool = True

    @model_validator(mode="after")
    def valid_book(self) -> "BookSnapshot":
        if not self.bids or not self.asks:
            raise ValueError("both book sides are required")
        if any(a.price > b.price for a, b in zip(self.asks, self.asks[1:], strict=False)):
            raise ValueError("asks must be sorted ascending")
        if any(a.price < b.price for a, b in zip(self.bids, self.bids[1:], strict=False)):
            raise ValueError("bids must be sorted descending")
        if self.bids[0].price >= self.asks[0].price:
            raise ValueError("book is crossed or locked")
        return self

    @property
    def age_ms(self) -> Decimal:
        return max(
            Decimal("0"),
            Decimal(str((self.received_at - self.exchange_timestamp).total_seconds() * 1000)),
        )


class VWAPResult(BaseModel):
    vwap: Decimal
    requested_notional: Decimal
    executable_notional: Decimal
    quantity: Decimal
    levels_consumed: int
    best_price: Decimal
    slippage_pct: Decimal
    liquidity_ratio: Decimal
    complete: bool


class ArbitrageLeg(BaseModel):
    exchange: ExchangeName
    market: MarketType
    side: str
    best_price: Decimal
    vwap: Decimal
    fee_pct: Decimal
    quantity: Decimal


class ArbitrageEconomics(BaseModel):
    target_notional: Decimal
    executable_notional: Decimal
    gross_spread_pct: Decimal
    fees_pct: Decimal
    slippage_pct: Decimal
    safety_buffer_pct: Decimal
    funding_effect_pct: Decimal = Decimal("0")
    expected_net_edge_pct: Decimal
    expected_net_pnl: Decimal
    buy_vwap: Decimal = Decimal("0")
    sell_vwap: Decimal = Decimal("0")
    quantity: Decimal = Decimal("0")


class ScoreComponent(BaseModel):
    name: str
    score: Decimal
    max_score: Decimal
    reasons: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()


class ArbitrageOpportunity(BaseModel):
    opportunity_id: str
    fingerprint: str
    detected_at: datetime
    last_updated_at: datetime
    symbol: str
    arbitrage_type: ArbitrageType
    status: ArbitrageStatus
    classification: ArbitrageClassification
    buy_leg: ArbitrageLeg
    sell_leg: ArbitrageLeg
    economics: ArbitrageEconomics
    quote_age_buy_ms: Decimal
    quote_age_sell_ms: Decimal
    timestamp_skew_ms: Decimal
    persistence_ms: Decimal = Decimal("0")
    consecutive_updates: int = 1
    spread_stability: Decimal = Decimal("0")
    confidence_score: Decimal = Decimal("0")
    score_components: tuple[ScoreComponent, ...] = ()
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
