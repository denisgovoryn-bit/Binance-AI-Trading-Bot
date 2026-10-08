from datetime import UTC, datetime, timedelta

import pandas as pd

from app.market.models import Timeframe
from app.market.structure import (
    BreakoutState,
    PriceZone,
    RetestState,
    StructureState,
    SwingKind,
    SwingLabel,
    ZoneKind,
    build_zones,
    classify_structure,
    detect_breakout,
    detect_range,
    detect_retest,
    detect_swings,
)


def frame(highs: list[float], lows: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(highs), freq="min", tz="UTC")
    closes = closes or [(high + low) / 2 for high, low in zip(highs, lows, strict=True)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100.0] * len(highs),
        },
        index=index,
    )


def test_swing_detection_and_higher_high_higher_low_labels() -> None:
    data = frame([2, 5, 3, 6, 4, 7, 5], [1, 2, 0, 3, 1, 4, 2])
    swings, _ = detect_swings(data, window=1)
    highs = [point for point in swings if point.kind is SwingKind.HIGH]
    lows = [point for point in swings if point.kind is SwingKind.LOW]
    assert highs[-1].label is SwingLabel.HIGHER_HIGH
    assert lows[-1].label is SwingLabel.HIGHER_LOW
    assert classify_structure(swings).state is StructureState.BULLISH


def test_lower_high_lower_low_classify_bearish() -> None:
    data = frame([8, 10, 7, 9, 6, 8, 5], [6, 8, 5, 7, 4, 6, 3])
    swings, _ = detect_swings(data, window=1)
    assert classify_structure(swings).state is StructureState.BEARISH


def test_realtime_pivot_is_developing_until_future_window_arrives() -> None:
    initial = frame([1, 2, 3], [0, 1, 2])
    confirmed, developing = detect_swings(initial, window=2)
    assert not confirmed
    assert any(point.index == 2 and not point.confirmed for point in developing)

    extended = frame([1, 2, 3, 2, 1], [0, 1, 2, 1, 0])
    confirmed, _ = detect_swings(extended, window=2)
    pivot = next(point for point in confirmed if point.index == 2 and point.kind is SwingKind.HIGH)
    assert pivot.confirmation_index == 4


def test_appending_unavailable_future_cannot_change_prior_realtime_result() -> None:
    data = frame([1, 3, 2, 4, 3, 5], [0, 1, 0.5, 2, 1.5, 3])
    prefix_swings, _ = detect_swings(data.iloc[:5], window=1)
    full_swings, _ = detect_swings(data, window=1)
    prefix_known = [
        (point.index, point.kind)
        for point in prefix_swings
        if point.confirmation_index is not None and point.confirmation_index < 5
    ]
    full_known = [
        (point.index, point.kind)
        for point in full_swings
        if point.confirmation_index is not None and point.confirmation_index < 5
    ]
    assert prefix_known == full_known


def zone(kind: ZoneKind = ZoneKind.RESISTANCE) -> PriceZone:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return PriceZone(
        kind=kind,
        lower=99,
        upper=101,
        touches=3,
        first_seen=now,
        last_seen=now + timedelta(hours=1),
        strength=80,
        timeframe=Timeframe.M15,
        broken=False,
    )


def test_zone_generation_clusters_repeated_reactions() -> None:
    data = frame([2, 5, 3, 5.1, 3, 5.05, 4], [1, 2, 0.9, 2, 1.0, 2, 1.1])
    swings, _ = detect_swings(data, 1)
    zones = build_zones(swings, Timeframe.M1, atr_value=1, current_price=3, tolerance_atr=0.5)
    resistance = max(
        (item for item in zones if item.kind is ZoneKind.RESISTANCE), key=lambda item: item.touches
    )
    support = max(
        (item for item in zones if item.kind is ZoneKind.SUPPORT), key=lambda item: item.touches
    )
    assert resistance.touches == 3
    assert support.touches >= 2
    assert resistance.lower < resistance.midpoint < resistance.upper


def test_confirmed_and_failed_breakouts_are_close_based() -> None:
    confirmed_frame = frame([100, 102, 103], [98, 100, 101], [100, 102, 102.5])
    confirmed = detect_breakout(
        confirmed_frame, (zone(),), atr_value=2, persistence=2, atr_threshold=0.1
    )
    failed_frame = frame([100, 103], [98, 99], [100, 100])
    failed = detect_breakout(failed_frame, (zone(),), atr_value=2, persistence=2, atr_threshold=0.1)
    assert confirmed.state is BreakoutState.CONFIRMED
    assert failed.state is BreakoutState.FAILED


def test_retest_held_and_failed() -> None:
    breakout_frame = frame([100, 102, 103], [98, 100, 100], [100, 102, 102.5])
    breakout = detect_breakout(
        breakout_frame, (zone(),), atr_value=2, persistence=2, atr_threshold=0.1
    )
    held = detect_retest(frame([103], [100], [102]), breakout)
    failed = detect_retest(frame([102], [98], [100]), breakout)
    assert held.state is RetestState.HELD
    assert failed.state is RetestState.FAILED


def test_range_detection_uses_width_and_low_adx() -> None:
    data = frame([101] * 20, [99] * 20, [100] * 20)
    result = detect_range(data, atr_value=1, adx_value=10, lookback=20, maximum_width_atr=6)
    assert result.in_range
    assert result.range_low == 99
    assert result.range_high == 101
    assert result.strength > 0
