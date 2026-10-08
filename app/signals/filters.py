"""Explainable hard filters and timeframe selection."""

from datetime import datetime

from pydantic import BaseModel

from app.indicators.models import TrendState, VolatilityRegime
from app.market.analysis import MarketAnalysis, TimeframeAnalysis
from app.market.models import MarketType, Timeframe
from app.market.structure import StructureState
from app.signals.config import SignalConfig
from app.signals.models import SignalDirection, TimeframeAlignment

PRIMARY_ORDER = (Timeframe.M15, Timeframe.M5, Timeframe.H1, Timeframe.M1, Timeframe.H4)
HIGHER_TIMEFRAMES = (Timeframe.H4, Timeframe.H1)


class FilterResult(BaseModel):
    accepted: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def select_primary(analysis: MarketAnalysis) -> TimeframeAnalysis:
    for timeframe in PRIMARY_ORDER:
        if timeframe in analysis.timeframes:
            return analysis.timeframes[timeframe]
    raise ValueError("market analysis has no supported timeframe")


def apply_preconstruction_filters(
    market: MarketAnalysis,
    primary: TimeframeAnalysis,
    direction: SignalDirection,
    evaluated_at: datetime,
    config: SignalConfig,
) -> FilterResult:
    rejected: list[str] = []
    warnings: list[str] = []
    if not market.timeframes:
        rejected.append("market analysis contains no timeframes")
    if any(
        (item.symbol, item.exchange, item.market) != (market.symbol, market.exchange, market.market)
        for item in market.timeframes.values()
    ):
        rejected.append("market analysis contains mismatched instrument metadata")
    if primary.data_quality != "VALID":
        rejected.append(f"primary timeframe data quality is {primary.data_quality}")
    if any(item.data_quality != "VALID" for item in market.timeframes.values()):
        rejected.append("one or more supplied timeframes have degraded history")
    age = (evaluated_at - market.timestamp).total_seconds()
    stale_limit = _timeframe_seconds(primary.timeframe) + config.stale_after_seconds
    if age > stale_limit:
        rejected.append(f"analysis is stale by {age:.0f} seconds; limit is {stale_limit}")
    if age < -5:
        rejected.append("analysis timestamp is in the future")
    if primary.volatility.regime is VolatilityRegime.EXTREME:
        rejected.append("primary timeframe volatility regime is EXTREME")
    expected_structure = StructureState.BULLISH if direction.bullish else StructureState.BEARISH
    opposite_structure = StructureState.BEARISH if direction.bullish else StructureState.BULLISH
    expected_trends = (
        {TrendState.BULLISH, TrendState.STRONG_BULLISH}
        if direction.bullish
        else {TrendState.BEARISH, TrendState.STRONG_BEARISH}
    )
    if primary.structure.state is opposite_structure and primary.trend.state in expected_trends:
        rejected.append("primary trend and confirmed market structure conflict")
    if market.market is MarketType.SPOT and direction is SignalDirection.SHORT:
        rejected.append("Spot markets cannot generate SHORT setups")
    if primary.structure.state is StructureState.INSUFFICIENT:
        rejected.append("confirmed structure is insufficient for structural risk placement")
    if primary.structure.state is not expected_structure:
        warnings.append(
            f"primary structure is {primary.structure.state.value}, not {expected_structure.value}"
        )
    return FilterResult(accepted=not rejected, reasons=tuple(rejected), warnings=tuple(warnings))


def timeframe_alignment(
    market: MarketAnalysis, direction: SignalDirection
) -> tuple[TimeframeAlignment, tuple[str, ...]]:
    available = [market.timeframes[item] for item in HIGHER_TIMEFRAMES if item in market.timeframes]
    if not available:
        return TimeframeAlignment.PARTIALLY_ALIGNED, ("no 1h/4h context supplied",)
    expected = (
        {TrendState.BULLISH, TrendState.STRONG_BULLISH}
        if direction.bullish
        else {TrendState.BEARISH, TrendState.STRONG_BEARISH}
    )
    opposite = (
        {TrendState.BEARISH, TrendState.STRONG_BEARISH}
        if direction.bullish
        else {TrendState.BULLISH, TrendState.STRONG_BULLISH}
    )
    aligned = sum(item.trend.state in expected for item in available)
    conflicting = sum(item.trend.state in opposite for item in available)
    reasons = tuple(
        f"{item.timeframe.value} trend is {item.trend.state.value}" for item in available
    )
    if aligned == len(available):
        return TimeframeAlignment.ALIGNED, reasons
    if conflicting == len(available):
        return TimeframeAlignment.CONFLICTING, reasons
    return TimeframeAlignment.PARTIALLY_ALIGNED, reasons


def _timeframe_seconds(timeframe: Timeframe) -> int:
    return {
        Timeframe.M1: 60,
        Timeframe.M5: 300,
        Timeframe.M15: 900,
        Timeframe.H1: 3_600,
        Timeframe.H4: 14_400,
    }[timeframe]
