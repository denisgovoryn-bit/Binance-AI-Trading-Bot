"""Structure-derived entry-zone construction."""

from app.market.analysis import TimeframeAnalysis
from app.market.structure import BreakoutState, RetestState, SwingKind, ZoneKind
from app.signals.config import SignalConfig
from app.signals.models import EntryType, EntryZone, SetupTag, SignalDirection


def build_entry(
    analysis: TimeframeAnalysis, direction: SignalDirection, config: SignalConfig
) -> tuple[EntryZone | None, tuple[SetupTag, ...], str | None]:
    atr_value = analysis.indicators.atr.absolute.current
    if atr_value is None or atr_value <= 0:
        return None, (), "ATR is unavailable; entry distance cannot be normalized"
    bullish = direction.bullish
    breakout_direction = "UP" if bullish else "DOWN"
    breakout = analysis.breakout
    if breakout.state is BreakoutState.FAILED and breakout.direction == breakout_direction:
        return (
            None,
            (),
            f"{breakout_direction.lower()} breakout failed and closed back inside its zone",
        )
    if breakout.zone is not None and breakout.direction == breakout_direction:
        if breakout.state is BreakoutState.CONFIRMED:
            entry_type = (
                EntryType.RETEST
                if analysis.retest.state is RetestState.HELD
                else EntryType.BREAKOUT
            )
            tag = SetupTag.RETEST if entry_type is EntryType.RETEST else SetupTag.BREAKOUT
            return _zone_entry(
                analysis,
                breakout.zone.lower,
                breakout.zone.upper,
                entry_type,
                f"entry derived from confirmed {breakout_direction.lower()} breakout zone",
                atr_value,
                config,
                (tag,),
            )
    desired_kind = ZoneKind.SUPPORT if bullish else ZoneKind.RESISTANCE
    valid_zones = [
        zone
        for zone in analysis.zones
        if zone.kind is desired_kind
        and not zone.broken
        and (
            zone.midpoint <= analysis.current_price
            if bullish
            else zone.midpoint >= analysis.current_price
        )
    ]
    if valid_zones:
        selected = min(valid_zones, key=lambda zone: abs(zone.midpoint - analysis.current_price))
        entry_type = EntryType.SUPPORT_REACTION if bullish else EntryType.RESISTANCE_REACTION
        return _zone_entry(
            analysis,
            selected.lower,
            selected.upper,
            entry_type,
            f"entry derived from nearest unbroken {desired_kind.value.lower()} zone with "
            f"{selected.touches} touches",
            atr_value,
            config,
            (SetupTag.PULLBACK, SetupTag.TREND_CONTINUATION),
        )
    swing_kind = SwingKind.LOW if bullish else SwingKind.HIGH
    swings = [point for point in analysis.structure.swings if point.kind is swing_kind]
    if not swings:
        return (
            None,
            (),
            f"no confirmed structural {swing_kind.value.lower()} for entry construction",
        )
    swing = swings[-1]
    half_width = atr_value * 0.15
    return _zone_entry(
        analysis,
        swing.price - half_width,
        swing.price + half_width,
        EntryType.PULLBACK,
        f"entry centered on latest confirmed swing {swing_kind.value.lower()}",
        atr_value,
        config,
        (SetupTag.PULLBACK,),
    )


def _zone_entry(
    analysis: TimeframeAnalysis,
    low: float,
    high: float,
    entry_type: EntryType,
    reason: str,
    atr_value: float,
    config: SignalConfig,
    tags: tuple[SetupTag, ...],
) -> tuple[EntryZone | None, tuple[SetupTag, ...], str | None]:
    ideal = (low + high) / 2
    distance_atr = abs(analysis.current_price - ideal) / atr_value
    if distance_atr > config.max_entry_extension_atr:
        return (
            None,
            (),
            f"entry is {distance_atr:.2f} ATR from market, above the "
            f"{config.max_entry_extension_atr:.2f} ATR limit",
        )
    return (
        EntryZone(
            low=low,
            high=high,
            ideal_entry=ideal,
            entry_type=entry_type,
            reason=reason,
            distance_from_market_atr=distance_atr,
        ),
        tags,
        None,
    )
