"""Centralized Phase 3A signal-engine configuration."""

from pydantic import BaseModel, Field, model_validator

from app.config.settings import Settings


class ScoreWeights(BaseModel):
    trend: float = 20
    structure: float = 20
    momentum: float = 15
    volume: float = 10
    volatility: float = 10
    entry: float = 10
    higher_timeframe: float = 10
    btc_context: float = 5

    @model_validator(mode="after")
    def total_is_one_hundred(self) -> "ScoreWeights":
        if abs(sum(self.model_dump().values()) - 100) > 1e-9:
            raise ValueError("score weights must total 100")
        return self


class SignalConfig(BaseModel):
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    watchlist_threshold: float = Field(default=70, ge=0, le=100)
    strong_threshold: float = Field(default=80, ge=0, le=100)
    very_strong_threshold: float = Field(default=90, ge=0, le=100)
    stale_after_seconds: int = Field(default=300, ge=1)
    max_entry_extension_atr: float = Field(default=2.0, gt=0)
    stop_atr_buffer: float = Field(default=0.25, ge=0)
    max_stop_distance_atr: float = Field(default=4.0, gt=0)
    minimum_best_r: float = Field(default=1.5, gt=0)
    setup_expiry_minutes: int = Field(default=60, ge=1)
    target_max_count: int = Field(default=3, ge=1, le=3)
    target_min_spacing_atr: float = Field(default=0.25, gt=0)
    paper_target_allocations: tuple[float, ...] = (30, 30, 40)
    btc_context_weight: float = Field(default=5, ge=0, le=5)
    enabled_setup_types: tuple[str, ...] = (
        "BREAKOUT",
        "RETEST",
        "PULLBACK",
        "TREND_CONTINUATION",
    )

    @model_validator(mode="after")
    def validate_thresholds_and_allocations(self) -> "SignalConfig":
        if not self.watchlist_threshold < self.strong_threshold < self.very_strong_threshold:
            raise ValueError("signal thresholds must increase")
        if not self.paper_target_allocations or any(
            value <= 0 for value in self.paper_target_allocations
        ):
            raise ValueError("paper allocations must be positive")
        if self.btc_context_weight != self.weights.btc_context:
            raise ValueError("BTC context weight must match its score component maximum")
        if len(self.paper_target_allocations) < self.target_max_count:
            raise ValueError("paper allocations must cover configured target count")
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> "SignalConfig":
        return cls(
            weights=ScoreWeights(
                trend=settings.signal_weight_trend,
                structure=settings.signal_weight_structure,
                momentum=settings.signal_weight_momentum,
                volume=settings.signal_weight_volume,
                volatility=settings.signal_weight_volatility,
                entry=settings.signal_weight_entry,
                higher_timeframe=settings.signal_weight_higher_timeframe,
                btc_context=settings.signal_weight_btc_context,
            ),
            watchlist_threshold=settings.signal_watchlist_threshold,
            strong_threshold=max(
                settings.signal_strong_threshold,
                settings.pending_setup_min_score,
            ),
            very_strong_threshold=settings.signal_very_strong_threshold,
            stale_after_seconds=settings.signal_stale_after_seconds,
            max_entry_extension_atr=min(
                settings.max_entry_extension_atr,
                settings.max_distance_from_entry_zone_at_creation_atr,
            ),
            stop_atr_buffer=settings.stop_atr_buffer,
            max_stop_distance_atr=settings.max_stop_distance_atr,
            minimum_best_r=settings.minimum_acceptable_r,
            setup_expiry_minutes=settings.setup_expiry_minutes,
            target_max_count=settings.target_max_count,
            target_min_spacing_atr=settings.target_min_spacing_atr,
            paper_target_allocations=settings.paper_target_allocations,
            btc_context_weight=settings.signal_weight_btc_context,
            enabled_setup_types=settings.enabled_setup_types,
        )
