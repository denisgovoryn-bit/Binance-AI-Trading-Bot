"""Structurally ordered take-profit target generation."""

from app.market.analysis import TimeframeAnalysis
from app.market.structure import SwingKind, ZoneKind
from app.signals.config import SignalConfig
from app.signals.models import EntryZone, InvalidationLevel, SignalDirection, SignalTarget
from app.signals.risk_reward import r_multiple


def build_targets(
    analysis: TimeframeAnalysis,
    direction: SignalDirection,
    entry: EntryZone,
    invalidation: InvalidationLevel,
    config: SignalConfig,
) -> tuple[tuple[SignalTarget, ...], str | None]:
    atr_value = analysis.indicators.atr.absolute.current
    if atr_value is None or atr_value <= 0:
        return (), "ATR is unavailable for target validation"
    bullish = direction.bullish
    opposing_kind = ZoneKind.RESISTANCE if bullish else ZoneKind.SUPPORT
    candidates: list[tuple[float, str, str]] = []
    for zone in analysis.zones:
        if zone.kind is opposing_kind and (
            zone.midpoint > entry.high if bullish else zone.midpoint < entry.low
        ):
            candidates.append(
                (
                    zone.midpoint,
                    f"{opposing_kind.value.lower()} zone",
                    f"historical zone with {zone.touches} touches",
                )
            )
    swing_kind = SwingKind.HIGH if bullish else SwingKind.LOW
    for swing in analysis.structure.swings:
        if swing.kind is swing_kind and (
            swing.price > entry.high if bullish else swing.price < entry.low
        ):
            candidates.append(
                (
                    swing.price,
                    f"confirmed swing {swing_kind.value.lower()}",
                    "previous confirmed structural extreme",
                )
            )
    if (
        analysis.range.in_range
        and analysis.range.range_low is not None
        and analysis.range.range_high is not None
    ):
        width = analysis.range.range_high - analysis.range.range_low
        projection = (
            analysis.range.range_high + width if bullish else analysis.range.range_low - width
        )
        if projection > entry.high if bullish else projection < entry.low:
            candidates.append(
                (projection, "range projection", "one measured range width beyond the boundary")
            )
    candidates.sort(key=lambda item: item[0], reverse=not bullish)
    unique: list[tuple[float, str, str]] = []
    for candidate in candidates:
        if (
            not unique
            or abs(candidate[0] - unique[-1][0]) >= atr_value * config.target_min_spacing_atr
        ):
            unique.append(candidate)
    targets: list[SignalTarget] = []
    for price, source, reason in unique[: config.target_max_count]:
        multiple = r_multiple(price, entry, invalidation, direction)
        if multiple is None:
            continue
        targets.append(
            SignalTarget(
                name=f"TP{len(targets) + 1}",
                price=price,
                source=source,
                reason=reason,
                r_multiple=multiple,
            )
        )
    if not targets:
        return (), "no structurally valid target exists beyond entry"
    return tuple(targets), None


def targets_are_ordered(targets: tuple[SignalTarget, ...], direction: SignalDirection) -> bool:
    prices = [target.price for target in targets]
    return prices == sorted(prices, reverse=not direction.bullish) and len(prices) == len(
        set(prices)
    )
