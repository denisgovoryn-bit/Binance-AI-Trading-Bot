from app.market.structure import ZoneKind
from app.signals.config import SignalConfig
from app.signals.entry import build_entry
from app.signals.models import EntryType, EntryZone, InvalidationLevel, SignalDirection
from app.signals.risk_reward import build_risk_reward, r_multiple
from app.signals.stop_loss import build_invalidation
from app.signals.targets import build_targets, targets_are_ordered

from .conftest import make_timeframe


def test_entry_range_and_long_stop_use_atr_buffer() -> None:
    analysis = make_timeframe()
    config = SignalConfig(stop_atr_buffer=0.25)
    entry, _, error = build_entry(analysis, SignalDirection.LONG, config)
    assert error is None and entry is not None
    stop, error = build_invalidation(analysis, SignalDirection.LONG, entry, config)
    assert error is None and stop is not None
    assert entry.entry_type is EntryType.SUPPORT_REACTION
    assert (entry.low, entry.high, entry.ideal_entry) == (98, 99, 98.5)
    assert stop.price == 97.5
    assert stop.reason.endswith("0.25 ATR buffer")


def test_short_stop_is_above_resistance_with_atr_buffer() -> None:
    analysis = make_timeframe(bullish=False)
    config = SignalConfig(stop_atr_buffer=0.5)
    entry, _, _ = build_entry(analysis, SignalDirection.SHORT, config)
    assert entry is not None
    stop, error = build_invalidation(analysis, SignalDirection.SHORT, entry, config)
    assert error is None and stop is not None
    assert stop.price == 103
    assert stop.price > entry.high


def test_invalid_stop_is_rejected_without_structure() -> None:
    analysis = make_timeframe()
    analysis.zones = tuple(zone for zone in analysis.zones if zone.kind is ZoneKind.RESISTANCE)
    analysis.structure.swings = tuple(
        swing for swing in analysis.structure.swings if swing.price >= 98
    )
    entry = EntryZone(
        low=98,
        high=99,
        ideal_entry=98.5,
        entry_type=EntryType.PULLBACK,
        reason="test",
        distance_from_market_atr=0,
    )
    stop, error = build_invalidation(analysis, SignalDirection.LONG, entry, SignalConfig())
    assert stop is None
    assert error is not None


def test_target_counts_ordering_and_invalid_target_math() -> None:
    analysis = make_timeframe()
    entry = EntryZone(
        low=98,
        high=99,
        ideal_entry=98.5,
        entry_type=EntryType.PULLBACK,
        reason="test",
        distance_from_market_atr=0,
    )
    stop = InvalidationLevel(price=97.5, reason="test", atr_buffer=0.5, distance_atr=0.5)
    one, _ = build_targets(
        analysis, SignalDirection.LONG, entry, stop, SignalConfig(target_max_count=1)
    )
    two, _ = build_targets(
        analysis, SignalDirection.LONG, entry, stop, SignalConfig(target_max_count=2)
    )
    three, _ = build_targets(
        analysis, SignalDirection.LONG, entry, stop, SignalConfig(target_max_count=3)
    )
    assert len(one) == 1
    assert len(two) == 2
    assert len(three) == 3
    assert targets_are_ordered(three, SignalDirection.LONG)
    assert r_multiple(97, entry, stop, SignalDirection.LONG) is None


def test_r_calculation_and_paper_allocations_normalize() -> None:
    analysis = make_timeframe()
    entry = EntryZone(
        low=98,
        high=99,
        ideal_entry=98.5,
        entry_type=EntryType.PULLBACK,
        reason="test",
        distance_from_market_atr=0,
    )
    stop = InvalidationLevel(price=97.5, reason="test", atr_buffer=0.5, distance_atr=0.5)
    targets, _ = build_targets(
        analysis, SignalDirection.LONG, entry, stop, SignalConfig(target_max_count=2)
    )
    metrics, plan = build_risk_reward(entry, stop, targets, (30, 30, 40))
    assert metrics.risk_distance == 1
    assert metrics.target_r_multiples["TP1"] == targets[0].price - 98.5
    assert sum(item.percentage for item in plan) == 100
    assert [item.percentage for item in plan] == [50, 50]
