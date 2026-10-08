"""Typed domain models for directional research setups."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.indicators.models import TrendState, VolatilityRegime
from app.market.models import ExchangeName, MarketType, Timeframe
from app.market.structure import StructureState


class SignalDirection(StrEnum):
    BUY = "BUY"
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def bullish(self) -> bool:
        return self in {SignalDirection.BUY, SignalDirection.LONG}


class SignalType(StrEnum):
    LONG_WATCH = "LONG_WATCH"
    SHORT_WATCH = "SHORT_WATCH"
    LONG_SETUP = "LONG_SETUP"
    SHORT_SETUP = "SHORT_SETUP"
    NO_TRADE = "NO_TRADE"


class SignalStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    REJECTED = "REJECTED"
    WATCHLIST = "WATCHLIST"
    WAITING_ENTRY = "WAITING_ENTRY"
    REVALIDATING = "REVALIDATING"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    ENTRY_CONFIRMED = "ENTRY_CONFIRMED"
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    TP1_REACHED = "TP1_REACHED"
    TP2_REACHED = "TP2_REACHED"
    TP3_REACHED = "TP3_REACHED"
    STOPPED = "STOPPED"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"


class SignalClassification(StrEnum):
    NO_TRADE = "NO_TRADE"
    WATCHLIST = "WATCHLIST"
    STRONG_SETUP = "STRONG_SETUP"
    VERY_STRONG_SETUP = "VERY_STRONG_SETUP"


class SetupTag(StrEnum):
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    PULLBACK = "PULLBACK"
    RANGE = "RANGE"
    TREND_CONTINUATION = "TREND_CONTINUATION"


class EntryType(StrEnum):
    RETEST = "RETEST"
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    SUPPORT_REACTION = "SUPPORT_REACTION"
    RESISTANCE_REACTION = "RESISTANCE_REACTION"
    RANGE_BREAKOUT = "RANGE_BREAKOUT"


class EntryZone(BaseModel):
    low: float
    high: float
    ideal_entry: float
    entry_type: EntryType
    reason: str
    distance_from_market_atr: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> "EntryZone":
        if self.low >= self.high:
            raise ValueError("entry low must be below entry high")
        if not self.low <= self.ideal_entry <= self.high:
            raise ValueError("ideal entry must be inside entry range")
        return self


class InvalidationLevel(BaseModel):
    price: float
    reason: str
    atr_buffer: float = Field(ge=0)
    distance_atr: float = Field(gt=0)


class SignalTarget(BaseModel):
    name: str
    price: float
    source: str
    reason: str
    r_multiple: float = Field(gt=0)


class RiskRewardMetrics(BaseModel):
    reference_entry: float
    risk_distance: float = Field(gt=0)
    target_r_multiples: dict[str, float]
    best_r: float = Field(gt=0)
    weighted_r: float = Field(gt=0)


class ScoreComponent(BaseModel):
    name: str
    score: float = Field(ge=0)
    max_score: float = Field(gt=0)
    reasons: tuple[str, ...] = ()
    penalties: tuple[str, ...] = ()

    @model_validator(mode="after")
    def score_within_bounds(self) -> "ScoreComponent":
        if self.score > self.max_score:
            raise ValueError("component score exceeds maximum")
        return self


class TimeframeAlignment(StrEnum):
    ALIGNED = "ALIGNED"
    PARTIALLY_ALIGNED = "PARTIALLY_ALIGNED"
    CONFLICTING = "CONFLICTING"


class PaperExitAllocation(BaseModel):
    target_name: str
    percentage: float = Field(gt=0, le=100)


class DirectionalSignal(BaseModel):
    signal_id: UUID
    created_at: datetime
    analysis_timestamp: datetime
    exchange: ExchangeName
    market_type: MarketType
    symbol: str
    primary_timeframe: Timeframe
    direction: SignalDirection | None
    signal_type: SignalType
    status: SignalStatus
    classification: SignalClassification
    confidence_score: float = Field(ge=0, le=100)
    score_components: tuple[ScoreComponent, ...]
    tags: tuple[SetupTag, ...] = ()
    entry_zone: EntryZone | None = None
    ideal_entry: float | None = None
    invalidation: InvalidationLevel | None = None
    invalidation_reason: str | None = None
    targets: tuple[SignalTarget, ...] = ()
    risk_reward: RiskRewardMetrics | None = None
    paper_exit_plan: tuple[PaperExitAllocation, ...] = ()
    market_regime: VolatilityRegime
    trend: TrendState
    structure: StructureState
    timeframe_alignment: TimeframeAlignment
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    rejection_reasons: tuple[str, ...] = ()
    supporting_metrics: dict[str, Any]
    data_quality: str
    expires_at: datetime | None = None

    @property
    def entry_mid(self) -> float | None:
        return self.entry_zone.ideal_entry if self.entry_zone else None
