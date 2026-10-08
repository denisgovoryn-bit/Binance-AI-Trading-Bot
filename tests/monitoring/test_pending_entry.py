from datetime import timedelta

import pytest

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.market.models import MarketType
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor
from app.signals.engine import DirectionalSignalEngine
from app.signals.models import SignalClassification
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


@pytest.fixture
async def pending_context(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        yield SignalRepository(session), settings
    await database.close()


async def pending(repo, settings, *, bullish=True, market=MarketType.FUTURES):
    analysis = make_market(bullish=bullish, market_type=market)
    signal = DirectionalSignalEngine().evaluate(analysis, evaluated_at=NOW)
    outcome = await SignalService(repo, settings).process(signal, evaluated_at=NOW)
    assert outcome.record is not None and signal.entry_zone is not None
    return outcome.record, signal


def quote(record, price, *, at=NOW, received=None, spread=0.0):
    half = spread / 2
    return PriceEvent(
        symbol=record.symbol,
        last=price,
        bid=price - half,
        ask=price + half,
        exchange_timestamp=at,
        received_at=received or at,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bullish", "market", "direction"),
    [
        (True, MarketType.FUTURES, "LONG"),
        (False, MarketType.FUTURES, "SHORT"),
        (True, MarketType.SPOT, "BUY"),
    ],
)
async def test_zone_touch_requires_revalidation(
    pending_context, bullish, market, direction
) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings, bullish=bullish, market=market)
    assert record.status == "WAITING_ENTRY" and record.direction == direction
    assert record.entry_reason and record.entry_basis
    assert record.entry_low < record.ideal_entry < record.entry_high
    event = quote(record, signal.ideal_entry)
    result = await SignalMonitor(repo, settings).process_price(record, event)
    assert result.events == ("REVALIDATION_STARTED",)
    assert record.status == "REVALIDATING"
    assert record.entry_confirmed_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("bullish", "market", "inside_side"),
    [
        (True, MarketType.FUTURES, "ask"),
        (False, MarketType.FUTURES, "bid"),
        (True, MarketType.SPOT, "ask"),
    ],
)
async def test_entry_uses_executable_side(pending_context, bullish, market, inside_side) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings, bullish=bullish, market=market)
    assert signal.entry_zone is not None
    inside = signal.ideal_entry
    outside = signal.entry_zone.high + 1 if inside_side == "ask" else signal.entry_zone.low - 1
    event = PriceEvent(
        symbol=record.symbol,
        last=inside,
        bid=inside if inside_side == "bid" else inside,
        ask=inside if inside_side == "ask" else inside,
        exchange_timestamp=NOW,
        received_at=NOW,
    )
    if inside_side == "ask":
        event = event.model_copy(update={"bid": min(inside, outside), "ask": inside})
    else:
        event = event.model_copy(update={"bid": inside, "ask": max(inside, outside)})
    result = await SignalMonitor(repo, settings).process_price(record, event)
    assert result.events == ("REVALIDATION_STARTED",)
    assert record.current_executable_price == inside


@pytest.mark.asyncio
@pytest.mark.parametrize(("bullish", "side"), [(True, "ask"), (False, "bid")])
async def test_last_inside_but_executable_side_outside_does_not_trigger(
    pending_context, bullish, side
) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings, bullish=bullish)
    assert signal.entry_zone is not None
    last = signal.ideal_entry
    if side == "ask":
        bid, ask = last, signal.entry_zone.high + 0.01
    else:
        bid, ask = signal.entry_zone.low - 0.01, last
    result = await SignalMonitor(repo, settings).process_price(
        record,
        PriceEvent(
            symbol=record.symbol,
            last=last,
            bid=bid,
            ask=ask,
            exchange_timestamp=NOW,
            received_at=NOW,
        ),
    )
    assert "REVALIDATION_STARTED" not in result.events
    assert record.status == "WAITING_ENTRY"


@pytest.mark.asyncio
async def test_valid_revalidation_confirms_once(pending_context) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    monitor = SignalMonitor(repo, settings)
    event = quote(record, signal.ideal_entry)
    await monitor.process_price(record, event)
    result = await monitor.complete_entry_revalidation(record, signal, event)
    assert result.events == ("ENTRY_CONFIRMED", "SIGNAL_ACTIVE")
    assert record.status == "ACTIVE" and record.confidence_delta_at_entry == 0
    duplicate = await monitor.complete_entry_revalidation(record, signal, event)
    assert duplicate.ignored_reason == "signal is not revalidating"
    assert sum(x.event_type == "ENTRY_CONFIRMED" for x in record.monitoring_events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            {"confidence_score": 60, "classification": SignalClassification.NO_TRADE},
            "confidence",
        ),
        ({"risk_reward": None}, "risk/reward"),
    ],
)
async def test_deteriorated_revalidation_never_enters(pending_context, change, reason) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    monitor = SignalMonitor(repo, settings)
    event = quote(record, signal.ideal_entry)
    await monitor.process_price(record, event)
    result = await monitor.complete_entry_revalidation(
        record, signal.model_copy(update=change), event
    )
    assert result.events == ("REVALIDATION_FAILED",)
    assert record.status == "INVALIDATED" and record.entry_confirmed_at is None
    assert reason in (record.revalidation_reason or "")


@pytest.mark.asyncio
async def test_current_executable_price_risk_reward_is_recomputed(pending_context) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    monitor = SignalMonitor(repo, settings)
    event = quote(record, signal.entry_zone.high)
    await monitor.process_price(record, event)
    targets = tuple(
        target.model_copy(update={"price": signal.entry_zone.high + 0.01 * index})
        for index, target in enumerate(signal.targets, 1)
    )
    result = await monitor.complete_entry_revalidation(
        record, signal.model_copy(update={"targets": targets}), event
    )
    assert result.events == ("REVALIDATION_FAILED",)
    assert "executable-price risk/reward" in (record.revalidation_reason or "")


@pytest.mark.asyncio
async def test_stale_quote_and_future_analysis_cannot_confirm(pending_context) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    monitor = SignalMonitor(repo, settings)
    stale = quote(
        record,
        signal.ideal_entry,
        received=NOW + timedelta(seconds=settings.max_entry_age_seconds + 1),
    )
    result = await monitor.process_price(record, stale)
    assert result.events == ("MONITORING_DEGRADED",)
    assert record.status == "WAITING_ENTRY"

    fresh = quote(record, signal.ideal_entry, at=NOW + timedelta(seconds=1))
    await monitor.process_price(record, fresh)
    future = signal.model_copy(
        update={"analysis_timestamp": fresh.received_at + timedelta(seconds=1)}
    )
    result = await monitor.complete_entry_revalidation(record, future, fresh)
    assert result.events == ("REVALIDATION_FAILED",)
    assert "later than the entry event" in (record.revalidation_reason or "")


@pytest.mark.asyncio
async def test_expiry_and_structural_invalidation(pending_context) -> None:
    repo, settings = pending_context
    expired, signal = await pending(repo, settings)
    assert expired.expires_at is not None
    result = await SignalMonitor(repo, settings).process_price(
        expired, quote(expired, signal.ideal_entry, at=expired.expires_at)
    )
    assert result.events == ("SETUP_EXPIRED",) and expired.status == "EXPIRED"
    assert expired.expiry_reason

    invalid, invalid_signal = await pending(
        repo,
        settings.model_copy(update={"signal_deduplication_cooldown_seconds": 0}),
        bullish=False,
    )
    assert invalid_signal.invalidation is not None
    result = await SignalMonitor(repo, settings).process_price(
        invalid, quote(invalid, invalid_signal.invalidation.price, at=NOW + timedelta(minutes=1))
    )
    assert result.events == ("SETUP_INVALIDATED",)
    assert invalid.status == "INVALIDATED" and invalid.entry_confirmed_at is None


@pytest.mark.asyncio
async def test_entry_boundaries_and_approaching_are_idempotent(pending_context) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    assert signal.entry_zone is not None
    monitor = SignalMonitor(repo, settings)
    atr = float(signal.supporting_metrics["atr"])
    approaching_price = signal.entry_zone.high + settings.entry_approaching_distance_atr * atr / 2
    first = await monitor.process_price(record, quote(record, approaching_price))
    second = await monitor.process_price(
        record, quote(record, approaching_price, at=NOW + timedelta(milliseconds=1))
    )
    assert first.events == ("ENTRY_APPROACHING",) and not second.events
    assert sum(x.event_type == "ENTRY_APPROACHING" for x in record.monitoring_events) == 1

    boundary = (
        signal.entry_zone.high if record.direction in {"BUY", "LONG"} else signal.entry_zone.low
    )
    touched = await monitor.process_price(
        record, quote(record, boundary, at=NOW + timedelta(seconds=1))
    )
    assert touched.events == ("REVALIDATION_STARTED",)


@pytest.mark.asyncio
async def test_interrupted_revalidation_recovers_once(pending_context) -> None:
    repo, settings = pending_context
    record, signal = await pending(repo, settings)
    await SignalMonitor(repo, settings).process_price(record, quote(record, signal.ideal_entry))
    assert record.status == "REVALIDATING"
    assert await repo.recover_interrupted_revalidations(NOW + timedelta(seconds=1)) == 1
    assert record.status == "WAITING_ENTRY"
    assert await repo.recover_interrupted_revalidations(NOW + timedelta(seconds=2)) == 0
