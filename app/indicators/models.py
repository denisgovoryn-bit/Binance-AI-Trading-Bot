"""Typed results and configuration for deterministic analysis."""

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class AnalysisConfig(BaseModel):
    """Centralized, documented Phase 2 calculation parameters."""

    ema_periods: tuple[int, ...] = (9, 20, 50, 100, 200)
    rsi_period: int = Field(default=14, ge=2)
    macd_fast: int = Field(default=12, ge=2)
    macd_slow: int = Field(default=26, ge=3)
    macd_signal: int = Field(default=9, ge=2)
    atr_period: int = Field(default=14, ge=2)
    adx_period: int = Field(default=14, ge=2)
    bollinger_period: int = Field(default=20, ge=2)
    bollinger_stddev: float = Field(default=2.0, gt=0)
    volume_baseline: int = Field(default=20, ge=3)
    swing_window: int = Field(default=2, ge=1)
    zone_tolerance_atr: float = Field(default=0.5, gt=0)
    breakout_atr_threshold: float = Field(default=0.1, ge=0)
    breakout_persistence: int = Field(default=2, ge=1)
    range_lookback: int = Field(default=20, ge=5)
    range_max_width_atr: float = Field(default=6.0, gt=0)
    volatility_lookback: int = Field(default=100, ge=20)

    @model_validator(mode="after")
    def validate_period_relationships(self) -> "AnalysisConfig":
        if self.macd_fast >= self.macd_slow:
            raise ValueError("MACD fast period must be less than slow period")
        if len(set(self.ema_periods)) != len(self.ema_periods) or min(self.ema_periods) < 2:
            raise ValueError("EMA periods must be unique integers greater than one")
        return self


class ScalarSeries(BaseModel):
    current: float | None
    series: tuple[float | None, ...]
    sufficient: bool
    required_history: int


class MACDResult(BaseModel):
    macd: ScalarSeries
    signal: ScalarSeries
    histogram: ScalarSeries
    histogram_change: float | None
    histogram_direction: str | None


class ATRResult(BaseModel):
    absolute: ScalarSeries
    percentage: ScalarSeries


class ADXResult(BaseModel):
    adx: ScalarSeries
    plus_di: ScalarSeries
    minus_di: ScalarSeries


class BollingerResult(BaseModel):
    middle: ScalarSeries
    upper: ScalarSeries
    lower: ScalarSeries
    width_percentage: ScalarSeries
    price_position: ScalarSeries


class VolumeState(StrEnum):
    EXPANSION = "EXPANSION"
    NORMAL = "NORMAL"
    CONTRACTION = "CONTRACTION"
    INSUFFICIENT = "INSUFFICIENT"


class VolumeResult(BaseModel):
    raw: float
    sma: ScalarSeries
    relative: ScalarSeries
    z_score: ScalarSeries
    state: VolumeState
    reasons: tuple[str, ...]


class MomentumResult(BaseModel):
    returns: dict[int, float | None]
    rate_of_change: ScalarSeries
    acceleration: float | None


class TrendState(StrEnum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


class TrendResult(BaseModel):
    state: TrendState
    reasons: tuple[str, ...]


class VolatilityRegime(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    INSUFFICIENT = "INSUFFICIENT"


class VolatilityResult(BaseModel):
    regime: VolatilityRegime
    atr_percentage: float | None
    percentile: float | None
    reasons: tuple[str, ...]
