"""Structural invalidation construction and validation."""

from app.market.analysis import TimeframeAnalysis
from app.market.structure import SwingKind, ZoneKind
from app.signals.config import SignalConfig
from app.signals.models import EntryZone, InvalidationLevel, SignalDirection


def build_invalidation(
    analysis: TimeframeAnalysis,
    direction: SignalDirection,
    entry: EntryZone,
    config: SignalConfig,
) -> tuple[InvalidationLevel | None, str | None]:
    atr_value = analysis.indicators.atr.absolute.current
    if atr_value is None or atr_value <= 0:
        return None, "ATR is unavailable for structural invalidation"
    bullish = direction.bullish
    zone_kind = ZoneKind.SUPPORT if bullish else ZoneKind.RESISTANCE
    zone_levels = [
        (zone.lower if bullish else zone.upper, f"{zone_kind.value.lower()} zone")
        for zone in analysis.zones
        if zone.kind is zone_kind
        and (zone.lower <= entry.low if bullish else zone.upper >= entry.high)
    ]
    swing_kind = SwingKind.LOW if bullish else SwingKind.HIGH
    swing_levels = [
        (point.price, f"confirmed swing {swing_kind.value.lower()}")
        for point in analysis.structure.swings
        if point.kind is swing_kind
        and (point.price < entry.low if bullish else point.price > entry.high)
    ]
    candidates = zone_levels + swing_levels
    if not candidates:
        return None, "no structural level exists on the invalidation side of entry"
    structural_price, source = (
        max(candidates, key=lambda item: item[0])
        if bullish
        else min(candidates, key=lambda item: item[0])
    )
    buffer = atr_value * config.stop_atr_buffer
    stop_price = structural_price - buffer if bullish else structural_price + buffer
    risk = entry.ideal_entry - stop_price if bullish else stop_price - entry.ideal_entry
    if (
        risk <= 0
        or (bullish and stop_price >= entry.low)
        or (not bullish and stop_price <= entry.high)
    ):
        return None, "structural stop lies inside or on the wrong side of the entry zone"
    distance_atr = risk / atr_value
    if distance_atr > config.max_stop_distance_atr:
        return (
            None,
            f"structural stop is {distance_atr:.2f} ATR away, above the "
            f"{config.max_stop_distance_atr:.2f} ATR limit",
        )
    return (
        InvalidationLevel(
            price=stop_price,
            reason=f"beyond {source} with {config.stop_atr_buffer:.2f} ATR buffer",
            atr_buffer=config.stop_atr_buffer,
            distance_atr=distance_atr,
        ),
        None,
    )
