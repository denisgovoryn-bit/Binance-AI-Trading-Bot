"""Phase 3A orchestration over precomputed Phase 2 analysis."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.indicators.models import TrendState, VolatilityRegime
from app.market.analysis import MarketAnalysis, TimeframeAnalysis
from app.market.models import MarketType, Timeframe
from app.market.structure import StructureState
from app.signals.config import SignalConfig
from app.signals.entry import build_entry
from app.signals.filters import apply_preconstruction_filters, select_primary, timeframe_alignment
from app.signals.models import (
    DirectionalSignal,
    EntryZone,
    InvalidationLevel,
    RiskRewardMetrics,
    ScoreComponent,
    SignalClassification,
    SignalDirection,
    SignalStatus,
    SignalTarget,
    SignalType,
    TimeframeAlignment,
)
from app.signals.risk_reward import build_risk_reward
from app.signals.scoring import classify_score, score_setup, total_score
from app.signals.stop_loss import build_invalidation
from app.signals.targets import build_targets, targets_are_ordered


class DirectionalSignalEngine:
    """Build deterministic directional setups; never executes orders."""

    def __init__(self, config: SignalConfig | None = None) -> None:
        self.config = config or SignalConfig()

    def evaluate(
        self,
        market: MarketAnalysis,
        *,
        btc_context: MarketAnalysis | None = None,
        evaluated_at: datetime | None = None,
    ) -> DirectionalSignal:
        now = evaluated_at or datetime.now(UTC)
        candidates = self.evaluate_candidates(market, btc_context=btc_context, evaluated_at=now)
        try:
            primary = select_primary(market)
        except ValueError:
            return candidates[0]
        return max(candidates, key=lambda item: _candidate_priority(item, primary))

    @staticmethod
    def select_candidate(
        market: MarketAnalysis, candidates: tuple[DirectionalSignal, ...]
    ) -> DirectionalSignal:
        """Select the live outcome from an already-audited candidate set."""

        try:
            primary = select_primary(market)
        except ValueError:
            return candidates[0]
        return max(candidates, key=lambda item: _candidate_priority(item, primary))

    def evaluate_candidates(
        self,
        market: MarketAnalysis,
        *,
        btc_context: MarketAnalysis | None = None,
        evaluated_at: datetime | None = None,
    ) -> tuple[DirectionalSignal, ...]:
        """Return every direction considered, for immutable diagnostic auditing."""

        now = evaluated_at or datetime.now(UTC)
        try:
            primary = select_primary(market)
        except ValueError as exc:
            return (self._empty_rejection(market, now, str(exc)),)
        directions = (
            (SignalDirection.BUY,)
            if market.market is MarketType.SPOT
            else (SignalDirection.LONG, SignalDirection.SHORT)
        )
        return tuple(
            self._evaluate_direction(market, primary, direction, btc_context, now)
            for direction in directions
        )

    def _evaluate_direction(
        self,
        market: MarketAnalysis,
        primary: TimeframeAnalysis,
        direction: SignalDirection,
        btc_context: MarketAnalysis | None,
        now: datetime,
    ) -> DirectionalSignal:
        config = self.config
        filters = apply_preconstruction_filters(market, primary, direction, now, config)
        alignment, _ = timeframe_alignment(market, direction)
        if not filters.accepted:
            return self._rejection(
                market, primary, direction, now, filters.reasons, filters.warnings, alignment
            )
        entry, tags, entry_error = build_entry(primary, direction, config)
        if entry is None:
            return self._rejection(
                market,
                primary,
                direction,
                now,
                (entry_error or "entry construction failed",),
                filters.warnings,
                alignment,
            )
        if not any(tag.value in config.enabled_setup_types for tag in tags):
            return self._rejection(
                market,
                primary,
                direction,
                now,
                ("constructed entry type is disabled by configuration",),
                filters.warnings,
                alignment,
                entry=entry,
            )
        invalidation, stop_error = build_invalidation(primary, direction, entry, config)
        if invalidation is None:
            return self._rejection(
                market,
                primary,
                direction,
                now,
                (stop_error or "invalidation construction failed",),
                filters.warnings,
                alignment,
                entry=entry,
            )
        if (direction.bullish and primary.current_price <= invalidation.price) or (
            not direction.bullish and primary.current_price >= invalidation.price
        ):
            return self._rejection(
                market,
                primary,
                direction,
                now,
                ("setup is already beyond its structural invalidation",),
                filters.warnings,
                alignment,
                entry=entry,
            )
        targets, target_error = build_targets(primary, direction, entry, invalidation, config)
        if not targets or not targets_are_ordered(targets, direction):
            return self._rejection(
                market,
                primary,
                direction,
                now,
                (target_error or "targets are invalid or incorrectly ordered",),
                filters.warnings,
                alignment,
                entry=entry,
            )
        risk_reward, exit_plan = build_risk_reward(
            entry, invalidation, targets, config.paper_target_allocations
        )
        components, alignment, score_warnings = score_setup(
            market, primary, direction, entry, config, btc_context
        )
        score = total_score(components)
        if risk_reward.best_r < config.minimum_best_r:
            return self._rejection(
                market,
                primary,
                direction,
                now,
                (
                    f"best structural target offers {risk_reward.best_r:.2f}R, below minimum "
                    f"{config.minimum_best_r:.2f}R",
                ),
                filters.warnings,
                alignment,
                entry=entry,
                diagnostic_score=score,
                score_components=components,
                diagnostic_warnings=score_warnings,
                invalidation=invalidation,
                targets=targets,
                risk_reward=risk_reward,
            )
        classification = classify_score(score, config)
        reasons = tuple(reason for component in components for reason in component.reasons)
        warnings = tuple(dict.fromkeys((*filters.warnings, *score_warnings)))
        supporting_metrics = _supporting_metrics(primary)
        supporting_metrics["entry_zone_state"] = (
            "ACTIONABLE_NOW"
            if entry.low <= primary.current_price <= entry.high
            else "PENDING_ENTRY"
        )
        supporting_metrics["distance_to_entry_zone_atr"] = _entry_distance_atr(
            primary.current_price,
            entry.low,
            entry.high,
            primary.indicators.atr.absolute.current,
        )
        rejection_reasons: tuple[str, ...]
        if classification is SignalClassification.NO_TRADE:
            signal_type = SignalType.NO_TRADE
            status = SignalStatus.REJECTED
            rejection_reasons = (
                f"score {score:.2f} is below watchlist threshold {config.watchlist_threshold:.2f}",
            )
        elif classification is SignalClassification.WATCHLIST:
            signal_type = SignalType.LONG_WATCH if direction.bullish else SignalType.SHORT_WATCH
            status = SignalStatus.WATCHLIST
            rejection_reasons = ()
        else:
            signal_type = SignalType.LONG_SETUP if direction.bullish else SignalType.SHORT_SETUP
            status = SignalStatus.WAITING_ENTRY
            rejection_reasons = ()
        return DirectionalSignal(
            signal_id=uuid4(),
            created_at=now,
            analysis_timestamp=market.timestamp,
            exchange=market.exchange,
            market_type=market.market,
            symbol=market.symbol,
            primary_timeframe=primary.timeframe,
            direction=direction,
            signal_type=signal_type,
            status=status,
            classification=classification,
            confidence_score=score,
            score_components=components,
            tags=tags,
            entry_zone=entry,
            ideal_entry=entry.ideal_entry,
            invalidation=invalidation,
            invalidation_reason=invalidation.reason,
            targets=targets,
            risk_reward=risk_reward,
            paper_exit_plan=exit_plan,
            market_regime=primary.volatility.regime,
            trend=primary.trend.state,
            structure=primary.structure.state,
            timeframe_alignment=alignment,
            reasons=reasons,
            warnings=warnings,
            rejection_reasons=rejection_reasons,
            supporting_metrics=supporting_metrics,
            data_quality=primary.data_quality,
            expires_at=now + timedelta(minutes=config.setup_expiry_minutes)
            if classification is not SignalClassification.NO_TRADE
            else None,
        )

    def _rejection(
        self,
        market: MarketAnalysis,
        primary: TimeframeAnalysis,
        direction: SignalDirection,
        now: datetime,
        reasons: tuple[str, ...],
        warnings: tuple[str, ...],
        alignment: TimeframeAlignment,
        *,
        entry: EntryZone | None = None,
        diagnostic_score: float | None = None,
        score_components: tuple[ScoreComponent, ...] = (),
        diagnostic_warnings: tuple[str, ...] = (),
        invalidation: InvalidationLevel | None = None,
        targets: tuple[SignalTarget, ...] = (),
        risk_reward: RiskRewardMetrics | None = None,
    ) -> DirectionalSignal:
        return DirectionalSignal(
            signal_id=uuid4(),
            created_at=now,
            analysis_timestamp=market.timestamp,
            exchange=market.exchange,
            market_type=market.market,
            symbol=market.symbol,
            primary_timeframe=primary.timeframe,
            direction=direction,
            signal_type=SignalType.NO_TRADE,
            status=SignalStatus.REJECTED,
            classification=SignalClassification.NO_TRADE,
            confidence_score=0,
            score_components=score_components,
            entry_zone=entry,
            ideal_entry=entry.ideal_entry if entry is not None else None,
            invalidation=invalidation,
            targets=targets,
            risk_reward=risk_reward,
            market_regime=primary.volatility.regime,
            trend=primary.trend.state,
            structure=primary.structure.state,
            timeframe_alignment=alignment,
            reasons=(),
            warnings=tuple(dict.fromkeys((*warnings, *diagnostic_warnings))),
            rejection_reasons=reasons,
            supporting_metrics=_rejection_metrics(primary, diagnostic_score),
            data_quality=primary.data_quality,
        )

    def _empty_rejection(
        self, market: MarketAnalysis, now: datetime, reason: str
    ) -> DirectionalSignal:
        return DirectionalSignal(
            signal_id=uuid4(),
            created_at=now,
            analysis_timestamp=market.timestamp,
            exchange=market.exchange,
            market_type=market.market,
            symbol=market.symbol,
            primary_timeframe=next(iter(market.timeframes), Timeframe.M15),
            direction=None,
            signal_type=SignalType.NO_TRADE,
            status=SignalStatus.REJECTED,
            classification=SignalClassification.NO_TRADE,
            confidence_score=0,
            score_components=(),
            market_regime=VolatilityRegime.INSUFFICIENT,
            trend=TrendState.NEUTRAL,
            structure=StructureState.INSUFFICIENT,
            timeframe_alignment=TimeframeAlignment.PARTIALLY_ALIGNED,
            reasons=(),
            warnings=(),
            rejection_reasons=(reason,),
            supporting_metrics={},
            data_quality="INVALID",
        )


def _supporting_metrics(primary: TimeframeAnalysis) -> dict[str, float | str | None]:
    return {
        "current_price": primary.current_price,
        "atr": primary.indicators.atr.absolute.current,
        "atr_percentage": primary.indicators.atr.percentage.current,
        "rsi": primary.indicators.rsi.current,
        "relative_volume": primary.volume.relative.current,
        "volatility_percentile": primary.volatility.percentile,
    }


def _candidate_priority(
    signal: DirectionalSignal, primary: TimeframeAnalysis
) -> tuple[bool, bool, float, int]:
    actionable = signal.classification is not SignalClassification.NO_TRADE
    bullish_trend = primary.trend.state in {TrendState.BULLISH, TrendState.STRONG_BULLISH}
    bearish_trend = primary.trend.state in {TrendState.BEARISH, TrendState.STRONG_BEARISH}
    direction_matches = bool(
        signal.direction
        and (
            (signal.direction.bullish and bullish_trend)
            or (not signal.direction.bullish and bearish_trend)
        )
    )
    return actionable, direction_matches, signal.confidence_score, -len(signal.rejection_reasons)


def _entry_distance_atr(price: float, low: float, high: float, atr: float | None) -> float | None:
    if atr is None or atr <= 0:
        return None
    distance = low - price if price < low else price - high if price > high else 0
    return distance / atr


def _rejection_metrics(
    primary: TimeframeAnalysis, diagnostic_score: float | None
) -> dict[str, float | str | None]:
    metrics = _supporting_metrics(primary)
    metrics["diagnostic_score_before_hard_rejection"] = diagnostic_score
    return metrics
