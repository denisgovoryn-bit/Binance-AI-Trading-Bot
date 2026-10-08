"""Deterministic component scoring for directional setups."""

from app.indicators.models import TrendState, VolatilityRegime, VolumeState
from app.market.analysis import MarketAnalysis, TimeframeAnalysis
from app.market.models import Timeframe
from app.market.structure import StructureState, ZoneKind
from app.signals.config import SignalConfig
from app.signals.filters import timeframe_alignment
from app.signals.models import (
    EntryZone,
    ScoreComponent,
    SignalClassification,
    SignalDirection,
    TimeframeAlignment,
)


def score_setup(
    market: MarketAnalysis,
    primary: TimeframeAnalysis,
    direction: SignalDirection,
    entry: EntryZone,
    config: SignalConfig,
    btc_context: MarketAnalysis | None = None,
) -> tuple[tuple[ScoreComponent, ...], TimeframeAlignment, tuple[str, ...]]:
    weights = config.weights
    alignment, alignment_reasons = timeframe_alignment(market, direction)
    components = (
        _trend(primary, direction, weights.trend),
        _structure(primary, direction, weights.structure),
        _momentum(primary, direction, weights.momentum),
        _volume(primary, weights.volume),
        _volatility(primary, weights.volatility),
        _entry(primary, direction, entry, weights.entry, config),
        _higher_timeframe(alignment, alignment_reasons, weights.higher_timeframe),
        _btc_context(market, direction, btc_context, config.btc_context_weight),
    )
    warnings = tuple(penalty for component in components for penalty in component.penalties)
    return components, alignment, warnings


def total_score(components: tuple[ScoreComponent, ...]) -> float:
    return round(sum(component.score for component in components), 2)


def classify_score(score: float, config: SignalConfig) -> SignalClassification:
    if score >= config.very_strong_threshold:
        return SignalClassification.VERY_STRONG_SETUP
    if score >= config.strong_threshold:
        return SignalClassification.STRONG_SETUP
    if score >= config.watchlist_threshold:
        return SignalClassification.WATCHLIST
    return SignalClassification.NO_TRADE


def _trend(
    primary: TimeframeAnalysis, direction: SignalDirection, maximum: float
) -> ScoreComponent:
    strong = TrendState.STRONG_BULLISH if direction.bullish else TrendState.STRONG_BEARISH
    ordinary = TrendState.BULLISH if direction.bullish else TrendState.BEARISH
    opposite = (
        {TrendState.BEARISH, TrendState.STRONG_BEARISH}
        if direction.bullish
        else {TrendState.BULLISH, TrendState.STRONG_BULLISH}
    )
    state = primary.trend.state
    reasons: tuple[str, ...]
    penalties: tuple[str, ...]
    if state is strong:
        score, reasons, penalties = (
            maximum,
            (f"{primary.timeframe.value} trend is {state.value}",),
            (),
        )
    elif state is ordinary:
        score, reasons, penalties = (
            maximum * 0.75,
            (f"{primary.timeframe.value} trend is {state.value}",),
            (),
        )
    elif state in opposite:
        score, reasons, penalties = 0.0, (), (f"{primary.timeframe.value} trend opposes direction",)
    else:
        score, reasons, penalties = maximum * 0.35, ("primary trend is neutral",), ()
    return ScoreComponent(
        name="trend", score=score, max_score=maximum, reasons=reasons, penalties=penalties
    )


def _structure(
    primary: TimeframeAnalysis, direction: SignalDirection, maximum: float
) -> ScoreComponent:
    expected = StructureState.BULLISH if direction.bullish else StructureState.BEARISH
    opposite = StructureState.BEARISH if direction.bullish else StructureState.BULLISH
    reasons: tuple[str, ...]
    penalties: tuple[str, ...]
    if primary.structure.state is expected:
        score, reasons, penalties = maximum, tuple(primary.structure.reasons), ()
    elif primary.structure.state is opposite:
        score, reasons, penalties = 0.0, (), ("confirmed structure opposes direction",)
    else:
        score, reasons, penalties = (
            maximum * 0.25,
            (f"structure is {primary.structure.state.value}",),
            (),
        )
    return ScoreComponent(
        name="structure", score=score, max_score=maximum, reasons=reasons, penalties=penalties
    )


def _momentum(
    primary: TimeframeAnalysis, direction: SignalDirection, maximum: float
) -> ScoreComponent:
    points = 0.0
    reasons: list[str] = []
    penalties: list[str] = []
    expected_positive = direction.bullish
    return_5 = primary.indicators.momentum.returns.get(5)
    if return_5 is not None and (return_5 > 0) is expected_positive:
        points += 0.4
        reasons.append("5-candle return supports direction")
    else:
        penalties.append("5-candle return does not support direction")
    histogram = primary.indicators.macd.histogram.current
    if histogram is not None and (histogram > 0) is expected_positive:
        points += 0.4
        reasons.append("MACD histogram supports direction")
    else:
        penalties.append("MACD histogram does not support direction")
    rsi = primary.indicators.rsi.current
    if rsi is not None and (45 <= rsi <= 75 if expected_positive else 25 <= rsi <= 55):
        points += 0.2
        reasons.append("RSI is directionally supportive without an extreme assumption")
    elif rsi is not None:
        penalties.append(f"RSI {rsi:.1f} is not in the supportive reference band")
    return ScoreComponent(
        name="momentum",
        score=maximum * points,
        max_score=maximum,
        reasons=tuple(reasons),
        penalties=tuple(penalties),
    )


def _volume(primary: TimeframeAnalysis, maximum: float) -> ScoreComponent:
    state = primary.volume.state
    fraction = {
        VolumeState.EXPANSION: 1.0,
        VolumeState.NORMAL: 0.7,
        VolumeState.CONTRACTION: 0.3,
        VolumeState.INSUFFICIENT: 0.0,
    }[state]
    penalties = (
        ("volume is contracted relative to baseline",) if state is VolumeState.CONTRACTION else ()
    )
    return ScoreComponent(
        name="volume",
        score=maximum * fraction,
        max_score=maximum,
        reasons=tuple(primary.volume.reasons),
        penalties=penalties,
    )


def _volatility(primary: TimeframeAnalysis, maximum: float) -> ScoreComponent:
    regime = primary.volatility.regime
    fraction = {
        VolatilityRegime.NORMAL: 1.0,
        VolatilityRegime.LOW: 0.75,
        VolatilityRegime.HIGH: 0.5,
        VolatilityRegime.EXTREME: 0.0,
        VolatilityRegime.INSUFFICIENT: 0.0,
    }[regime]
    penalties = (
        (f"volatility regime is {regime.value}",)
        if regime
        in {VolatilityRegime.HIGH, VolatilityRegime.EXTREME, VolatilityRegime.INSUFFICIENT}
        else ()
    )
    return ScoreComponent(
        name="volatility",
        score=maximum * fraction,
        max_score=maximum,
        reasons=tuple(primary.volatility.reasons),
        penalties=penalties,
    )


def _entry(
    primary: TimeframeAnalysis,
    direction: SignalDirection,
    entry: EntryZone,
    maximum: float,
    config: SignalConfig,
) -> ScoreComponent:
    fraction = max(0.0, 1 - entry.distance_from_market_atr / config.max_entry_extension_atr)
    reasons = [entry.reason, f"market is {entry.distance_from_market_atr:.2f} ATR from ideal entry"]
    penalties: list[str] = []
    if entry.distance_from_market_atr > 1:
        penalties.append("entry is more than 1 ATR from market")
    atr_value = primary.indicators.atr.absolute.current
    if atr_value is not None and atr_value > 0:
        opposing = ZoneKind.RESISTANCE if direction.bullish else ZoneKind.SUPPORT
        distances = [
            (zone.lower - entry.high if direction.bullish else entry.low - zone.upper) / atr_value
            for zone in primary.zones
            if zone.kind is opposing
            and (zone.lower > entry.high if direction.bullish else zone.upper < entry.low)
        ]
        if distances and min(distances) < 0.75:
            fraction = max(0.0, fraction - 0.25)
            penalties.append(f"opposing structure is only {min(distances):.2f} ATR beyond entry")
        elif distances:
            reasons.append("nearest opposing structure leaves at least 0.75 ATR of room")
    return ScoreComponent(
        name="entry",
        score=maximum * fraction,
        max_score=maximum,
        reasons=tuple(reasons),
        penalties=tuple(penalties),
    )


def _higher_timeframe(
    alignment: TimeframeAlignment, reasons: tuple[str, ...], maximum: float
) -> ScoreComponent:
    fraction = {
        TimeframeAlignment.ALIGNED: 1.0,
        TimeframeAlignment.PARTIALLY_ALIGNED: 0.5,
        TimeframeAlignment.CONFLICTING: 0.0,
    }[alignment]
    penalties = (
        ("higher timeframes conflict with setup direction",)
        if alignment is TimeframeAlignment.CONFLICTING
        else ()
    )
    return ScoreComponent(
        name="higher_timeframe",
        score=maximum * fraction,
        max_score=maximum,
        reasons=reasons,
        penalties=penalties,
    )


def _btc_context(
    market: MarketAnalysis, direction: SignalDirection, btc: MarketAnalysis | None, maximum: float
) -> ScoreComponent:
    if market.symbol.startswith("BTC"):
        return ScoreComponent(
            name="btc_context",
            score=maximum,
            max_score=maximum,
            reasons=("instrument is BTC; no external BTC conflict",),
        )
    if btc is None:
        return ScoreComponent(
            name="btc_context",
            score=maximum * 0.5,
            max_score=maximum,
            penalties=("BTC context unavailable",),
        )
    context = btc.timeframes.get(primary_context_timeframe(btc))
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
    if context is None:
        return ScoreComponent(
            name="btc_context",
            score=maximum * 0.5,
            max_score=maximum,
            penalties=("BTC context has no usable timeframe",),
        )
    if context.trend.state in expected:
        return ScoreComponent(
            name="btc_context",
            score=maximum,
            max_score=maximum,
            reasons=(f"BTC {context.timeframe.value} trend supports direction",),
        )
    if context.trend.state in opposite:
        return ScoreComponent(
            name="btc_context",
            score=0,
            max_score=maximum,
            penalties=(f"BTC {context.timeframe.value} trend strongly conflicts",),
        )
    return ScoreComponent(
        name="btc_context",
        score=maximum * 0.5,
        max_score=maximum,
        reasons=(f"BTC {context.timeframe.value} trend is neutral",),
    )


def primary_context_timeframe(market: MarketAnalysis) -> Timeframe:
    for timeframe in (Timeframe.H1, Timeframe.H4, Timeframe.M15):
        if timeframe in market.timeframes:
            return timeframe
    return next(iter(market.timeframes), Timeframe.M15)
