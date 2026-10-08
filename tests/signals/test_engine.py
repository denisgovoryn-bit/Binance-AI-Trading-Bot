from copy import deepcopy
from datetime import timedelta

from app.market.models import MarketType, Timeframe
from app.market.structure import BreakoutState, RetestState
from app.signals.config import SignalConfig
from app.signals.engine import DirectionalSignalEngine
from app.signals.models import (
    EntryType,
    SetupTag,
    SignalClassification,
    SignalDirection,
    SignalStatus,
    SignalType,
    TimeframeAlignment,
)

from .conftest import NOW, make_market


def test_bullish_long_setup_is_structural_and_explainable(bullish_market) -> None:
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    assert signal.direction is SignalDirection.LONG
    assert signal.classification is SignalClassification.VERY_STRONG_SETUP
    assert signal.status is SignalStatus.WAITING_ENTRY
    assert signal.entry_zone is not None
    assert signal.invalidation is not None
    assert signal.invalidation.price < signal.entry_zone.low
    assert signal.targets
    assert signal.reasons


def test_bearish_short_setup_has_stop_above_structure(bearish_market) -> None:
    signal = DirectionalSignalEngine().evaluate(bearish_market, evaluated_at=NOW)
    assert signal.direction is SignalDirection.SHORT
    assert signal.signal_type is SignalType.SHORT_SETUP
    assert signal.entry_zone is not None and signal.invalidation is not None
    assert signal.invalidation.price > signal.entry_zone.high
    assert [target.price for target in signal.targets] == sorted(
        (target.price for target in signal.targets), reverse=True
    )


def test_spot_never_generates_short() -> None:
    signal = DirectionalSignalEngine().evaluate(
        make_market(bullish=False, market_type=MarketType.SPOT), evaluated_at=NOW
    )
    assert signal.direction is SignalDirection.BUY
    assert signal.signal_type is SignalType.NO_TRADE


def test_no_trade_watchlist_strong_and_very_strong_classifications(bullish_market) -> None:
    very_strong = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    strong = DirectionalSignalEngine(
        SignalConfig(strong_threshold=80, very_strong_threshold=99)
    ).evaluate(bullish_market, evaluated_at=NOW)
    watch = DirectionalSignalEngine(
        SignalConfig(strong_threshold=99, very_strong_threshold=100)
    ).evaluate(bullish_market, evaluated_at=NOW)
    no_trade = DirectionalSignalEngine(
        SignalConfig(watchlist_threshold=97, strong_threshold=98, very_strong_threshold=99)
    ).evaluate(bullish_market, evaluated_at=NOW)
    assert very_strong.classification is SignalClassification.VERY_STRONG_SETUP
    assert strong.classification is SignalClassification.STRONG_SETUP
    assert watch.classification is SignalClassification.WATCHLIST
    assert no_trade.classification is SignalClassification.NO_TRADE


def test_conflicting_higher_timeframes_are_penalized(bullish_market) -> None:
    for timeframe in (Timeframe.H1, Timeframe.H4):
        bearish = make_market(bullish=False).timeframes[timeframe]
        bullish_market.timeframes[timeframe] = bearish
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    assert signal.timeframe_alignment is TimeframeAlignment.CONFLICTING
    component = next(item for item in signal.score_components if item.name == "higher_timeframe")
    assert component.score == 0
    assert component.penalties


def test_conflicting_btc_context_penalizes_altcoin_without_automatic_rejection() -> None:
    alt = make_market()
    alt.symbol = "ETHUSDT"
    for timeframe in alt.timeframes.values():
        timeframe.symbol = "ETHUSDT"
    bearish_btc = make_market(bullish=False)
    signal = DirectionalSignalEngine().evaluate(
        alt,
        btc_context=bearish_btc,
        evaluated_at=NOW,
    )
    component = next(item for item in signal.score_components if item.name == "btc_context")
    assert component.score == 0
    assert component.penalties


def test_mismatched_market_analysis_metadata_is_rejected(bullish_market) -> None:
    bullish_market.timeframes[Timeframe.H1].symbol = "ETHUSDT"
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    assert any("mismatched" in reason for reason in signal.rejection_reasons)


def test_breakout_and_retest_entries_are_tagged() -> None:
    breakout = DirectionalSignalEngine().evaluate(
        make_market(breakout_state=BreakoutState.CONFIRMED), evaluated_at=NOW
    )
    retest = DirectionalSignalEngine().evaluate(
        make_market(
            breakout_state=BreakoutState.CONFIRMED,
            retest_state=RetestState.HELD,
        ),
        evaluated_at=NOW,
    )
    assert breakout.entry_zone is not None
    assert breakout.entry_zone.entry_type is EntryType.BREAKOUT
    assert SetupTag.BREAKOUT in breakout.tags
    assert retest.entry_zone is not None
    assert retest.entry_zone.entry_type is EntryType.RETEST
    assert SetupTag.RETEST in retest.tags


def test_failed_breakout_and_overextended_entry_are_rejected() -> None:
    failed = DirectionalSignalEngine().evaluate(
        make_market(breakout_state=BreakoutState.FAILED), evaluated_at=NOW
    )
    extended = DirectionalSignalEngine(SignalConfig(max_entry_extension_atr=0.1)).evaluate(
        make_market(), evaluated_at=NOW
    )
    assert failed.classification is SignalClassification.NO_TRADE
    assert any("failed" in reason for reason in failed.rejection_reasons)
    assert any("ATR from market" in reason for reason in extended.rejection_reasons)


def test_degraded_history_and_poor_r_are_rejected() -> None:
    degraded = DirectionalSignalEngine().evaluate(
        make_market(data_quality="DEGRADED"), evaluated_at=NOW
    )
    poor_r = DirectionalSignalEngine(SignalConfig(minimum_best_r=100)).evaluate(
        make_market(), evaluated_at=NOW
    )
    assert any("degraded" in reason.lower() for reason in degraded.rejection_reasons)
    assert any("below minimum" in reason for reason in poor_r.rejection_reasons)


def test_stale_analysis_is_rejected_after_timeframe_interval_and_grace() -> None:
    market = make_market()
    signal = DirectionalSignalEngine().evaluate(
        market,
        evaluated_at=NOW + timedelta(minutes=21),
    )
    assert any("stale" in reason for reason in signal.rejection_reasons)


def test_score_components_are_bounded_and_total_is_explainable(bullish_market) -> None:
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    assert sum(component.max_score for component in signal.score_components) == 100
    assert signal.confidence_score == sum(component.score for component in signal.score_components)
    assert all(component.reasons or component.penalties for component in signal.score_components)


def test_engine_consumes_snapshot_without_future_mutation(bullish_market) -> None:
    original = deepcopy(bullish_market)
    future_copy = deepcopy(bullish_market)
    future_copy.timestamp = NOW.replace(hour=13)
    future_copy.timeframes[Timeframe.M15].current_price = 500
    before = DirectionalSignalEngine().evaluate(original, evaluated_at=NOW)
    after = DirectionalSignalEngine().evaluate(original, evaluated_at=NOW)
    assert before.analysis_timestamp == NOW
    assert before.confidence_score == after.confidence_score
    assert before.entry_zone == after.entry_zone
    assert before.targets == after.targets
