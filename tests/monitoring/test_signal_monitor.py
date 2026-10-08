from datetime import timedelta

import pytest

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.monitoring.models import CandlePriceEvent, PriceEvent
from app.monitoring.policies import breakeven_stop, structure_trailing_stop
from app.monitoring.signal_monitor import SignalMonitor
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


@pytest.fixture
def bullish_market():
    return make_market(bullish=True)


@pytest.fixture
def bearish_market():
    return make_market(bullish=False)


@pytest.fixture
async def monitor_context(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'monitor.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        repo = SignalRepository(session)
        yield repo, settings
    await database.close()


async def persisted(repo, settings, market):
    signal = DirectionalSignalEngine().evaluate(market, evaluated_at=NOW)
    outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
    assert outcome.record is not None
    return outcome.record, signal


def tick(symbol: str, price: float, at=NOW, *, bid=None, ask=None, received=None) -> PriceEvent:
    return PriceEvent(
        symbol=symbol,
        last=price,
        bid=bid,
        ask=ask,
        exchange_timestamp=at,
        received_at=received or at,
    )


async def confirm(monitor, record, signal, *, at=NOW):
    price = signal.ideal_entry or 100
    event = tick(record.symbol, price, at=at, bid=price, ask=price)
    touched = await monitor.process_price(record, event)
    assert touched.events == ("REVALIDATION_STARTED",)
    confirmed = await monitor.complete_entry_revalidation(record, signal, event)
    assert confirmed.events == ("ENTRY_CONFIRMED", "SIGNAL_ACTIVE")
    return confirmed


@pytest.mark.asyncio
async def test_long_entry_duplicate_and_all_targets_complete(
    monitor_context, bullish_market
) -> None:
    repo, settings = monitor_context
    record, signal = await persisted(repo, settings, bullish_market)
    assert signal.entry_zone is not None
    monitor = SignalMonitor(repo, settings)
    outside = await monitor.process_price(
        record, tick(record.symbol, signal.entry_zone.high + 1, ask=signal.entry_zone.high + 1)
    )
    assert outside.events in {(), ("ENTRY_APPROACHING",)}
    assert record.status == "WAITING_ENTRY"
    await confirm(monitor, record, signal)
    duplicate = await monitor.process_price(
        record, tick(record.symbol, signal.ideal_entry or 100, ask=signal.ideal_entry)
    )
    assert "ENTRY_CONFIRMED" not in duplicate.events
    assert signal.targets
    crossed = await monitor.process_price(
        record, tick(record.symbol, signal.targets[-1].price, at=NOW + timedelta(seconds=1))
    )
    expected = tuple(f"TP{i}_REACHED" for i in range(1, len(signal.targets) + 1))
    assert crossed.events[: len(expected)] == expected
    assert crossed.events[-1] == "SIGNAL_COMPLETED"
    assert record.status == "COMPLETED"
    assert sum(x.event_type == "TP1_REACHED" for x in record.monitoring_events) == 1


@pytest.mark.asyncio
async def test_short_entry_targets_and_stop_are_directional(
    monitor_context, bearish_market
) -> None:
    repo, settings = monitor_context
    record, signal = await persisted(repo, settings, bearish_market)
    monitor = SignalMonitor(repo, settings)
    await confirm(monitor, record, signal)
    assert record.status == "ACTIVE"
    assert signal.targets
    result = await monitor.process_price(
        record, tick(record.symbol, signal.targets[-1].price, at=NOW + timedelta(seconds=1))
    )
    assert "TP1_REACHED" in result.events and record.status == "COMPLETED"

    record2, signal2 = await persisted(
        repo,
        settings.model_copy(update={"signal_deduplication_cooldown_seconds": 0}),
        bearish_market,
    )
    await confirm(monitor, record2, signal2, at=NOW + timedelta(minutes=1))
    assert signal2.invalidation is not None
    stopped = await monitor.process_price(
        record2,
        tick(record2.symbol, signal2.invalidation.price, at=NOW + timedelta(minutes=1, seconds=1)),
    )
    assert stopped.events == ("STOP_REACHED",) and record2.status == "STOPPED"


@pytest.mark.asyncio
async def test_long_stop_terminal_out_of_order_and_stale_recovery(
    monitor_context, bullish_market
) -> None:
    repo, settings = monitor_context
    record, signal = await persisted(repo, settings, bullish_market)
    monitor = SignalMonitor(repo, settings)
    await confirm(monitor, record, signal)
    stale_at = NOW + timedelta(seconds=1)
    stale = await monitor.process_price(
        record, tick(record.symbol, 1, at=stale_at, received=stale_at + timedelta(seconds=10))
    )
    assert stale.events == ("MONITORING_DEGRADED",) and record.status == "ACTIVE"
    recovered = await monitor.process_price(
        record, tick(record.symbol, signal.ideal_entry or 100, at=NOW + timedelta(seconds=2))
    )
    assert recovered.events == ("MONITORING_RECOVERED",)
    old = await monitor.process_price(record, tick(record.symbol, 1, at=NOW - timedelta(seconds=1)))
    assert old.ignored_reason == "out-of-order event"
    assert signal.invalidation is not None
    stopped = await monitor.process_price(
        record, tick(record.symbol, signal.invalidation.price, at=NOW + timedelta(seconds=3))
    )
    assert stopped.events == ("STOP_REACHED",)
    ignored = await monitor.process_price(
        record, tick(record.symbol, signal.targets[-1].price, at=NOW + timedelta(seconds=4))
    )
    assert ignored.ignored_reason == "terminal signal"


@pytest.mark.asyncio
async def test_ambiguous_candle_does_not_choose_favorable_path(
    monitor_context, bullish_market
) -> None:
    repo, settings = monitor_context
    record, signal = await persisted(repo, settings, bullish_market)
    monitor = SignalMonitor(repo, settings)
    await confirm(monitor, record, signal)
    assert signal.invalidation is not None and signal.targets
    result = await monitor.process_candle(
        record,
        CandlePriceEvent(
            symbol=record.symbol,
            high=signal.targets[0].price + 1,
            low=signal.invalidation.price - 1,
            close=signal.ideal_entry or 100,
            exchange_timestamp=NOW + timedelta(minutes=15),
            received_at=NOW + timedelta(minutes=15),
        ),
    )
    assert result.events == ("AMBIGUOUS_INTRABAR",)
    assert record.status == "ACTIVE"


@pytest.mark.asyncio
async def test_confidence_weakening_recovery_and_history(monitor_context, bullish_market) -> None:
    repo, settings = monitor_context
    record, signal = await persisted(repo, settings, bullish_market)
    monitor = SignalMonitor(repo, settings)
    await confirm(monitor, record, signal)
    components = tuple(
        x.model_copy(update={"score": max(0, x.score - 5)})
        if x.name in {"structure", "momentum"}
        else x
        for x in signal.score_components
    )
    weak = signal.model_copy(
        update={"confidence_score": signal.confidence_score - 10, "score_components": components}
    )
    result = await monitor.refresh_confidence(record, weak, NOW + timedelta(minutes=5))
    assert "SIGNAL_WEAKENING" in result.events and record.status == "WEAKENING"
    recovered = signal.model_copy(update={"confidence_score": weak.confidence_score + 4})
    result = await monitor.refresh_confidence(record, recovered, NOW + timedelta(minutes=10))
    assert "SIGNAL_RECOVERED" in result.events and record.status == "ACTIVE"
    detail = await repo.get_signal(record.id)
    assert detail is not None
    assert any(x.event_type == "CONFIDENCE_UPDATED" for x in detail.monitoring_events)


def test_stop_policy_math_and_confirmed_structure_only() -> None:
    assert breakeven_stop(100, True, 10) == pytest.approx(100.1)
    assert breakeven_stop(100, False, 10) == pytest.approx(99.9)
    assert structure_trailing_stop(95, 98, True, 1) == 97
    assert structure_trailing_stop(95, 94, True, 1) is None


@pytest.mark.parametrize("target_count", [1, 2])
@pytest.mark.asyncio
async def test_final_available_target_completes_signal(
    monitor_context, bullish_market, target_count
) -> None:
    repo, settings = monitor_context
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    signal = signal.model_copy(
        update={"symbol": f"COUNT{target_count}USDT", "targets": signal.targets[:target_count]}
    )
    outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
    assert outcome.record is not None
    monitor = SignalMonitor(repo, settings)
    await confirm(monitor, outcome.record, signal)
    result = await monitor.process_price(
        outcome.record, tick(signal.symbol, signal.targets[-1].price, at=NOW + timedelta(seconds=1))
    )
    assert result.events[-1] == "SIGNAL_COMPLETED"
    assert outcome.record.status == "COMPLETED"


@pytest.mark.asyncio
async def test_configured_stop_management_policies(monitor_context, bullish_market) -> None:
    repo, base_settings = monitor_context
    signal = (
        DirectionalSignalEngine()
        .evaluate(bullish_market, evaluated_at=NOW)
        .model_copy(update={"symbol": "POLICYUSDT"})
    )
    settings = base_settings.model_copy(update={"stop_management_policy": "BREAKEVEN_AFTER_TP1"})
    outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
    assert outcome.record is not None and signal.targets
    monitor = SignalMonitor(repo, settings)
    entry = signal.ideal_entry or 100
    await confirm(monitor, outcome.record, signal)
    result = await monitor.process_price(
        outcome.record, tick(signal.symbol, signal.targets[0].price, at=NOW + timedelta(seconds=1))
    )
    assert "STOP_UPDATED" in result.events
    assert outcome.record.invalidation_price == pytest.approx(entry)

    trailing_settings = base_settings.model_copy(
        update={"stop_management_policy": "STRUCTURE_TRAILING"}
    )
    trailing = SignalMonitor(repo, trailing_settings)
    old = outcome.record.invalidation_price
    assert old is not None
    changed = await trailing.apply_confirmed_structure_trail(
        outcome.record, old + 2, 0.5, NOW + timedelta(seconds=2)
    )
    assert changed and outcome.record.invalidation_price == pytest.approx(old + 1.5)
