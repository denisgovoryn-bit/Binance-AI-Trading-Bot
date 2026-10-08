from datetime import timedelta

import pytest

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.signals.engine import DirectionalSignalEngine
from app.signals.identity import correlation_group_key, signal_fingerprint
from app.signals.lifecycle import InvalidSignalTransition, validate_transition
from app.signals.models import SignalClassification, SignalStatus
from app.signals.ranking import group_top
from app.signals.scanner import DirectionalScanner, ScanSummary
from app.signals.service import ServiceOutcome, SignalService
from tests.signals.conftest import NOW


def test_fingerprint_is_deterministic_and_ignores_score(bullish_market) -> None:
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    changed = signal.model_copy(update={"confidence_score": signal.confidence_score - 4})
    assert signal_fingerprint(signal) == signal_fingerprint(changed)
    assert correlation_group_key(signal) == correlation_group_key(changed)


def test_structural_new_setup_changes_fingerprint(bullish_market) -> None:
    signal = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    assert signal.entry_zone is not None
    entry = signal.entry_zone.model_copy(update={"low": signal.entry_zone.low - 1})
    assert signal_fingerprint(signal) != signal_fingerprint(
        signal.model_copy(update={"entry_zone": entry})
    )


def test_transition_matrix() -> None:
    validate_transition(SignalStatus.DISCOVERED, SignalStatus.WATCHLIST)
    validate_transition(SignalStatus.WATCHLIST, SignalStatus.WAITING_ENTRY)
    validate_transition(SignalStatus.WATCHLIST, SignalStatus.INVALIDATED)
    validate_transition(SignalStatus.WAITING_ENTRY, SignalStatus.EXPIRED)
    with pytest.raises(InvalidSignalTransition):
        validate_transition(SignalStatus.INVALIDATED, SignalStatus.WATCHLIST)


@pytest.fixture
async def repository(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'signals.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        yield SignalRepository(session), settings
    await database.close()


@pytest.mark.asyncio
async def test_dedup_material_updates_lifecycle_and_expiry(repository, bullish_market) -> None:
    repo, settings = repository
    service = SignalService(repo, settings)
    base = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    watch = base.model_copy(
        update={"classification": SignalClassification.WATCHLIST, "confidence_score": 76.0}
    )
    created = await service.process(watch, evaluated_at=NOW)
    assert created.action == "CREATED"
    assert created.record is not None and created.record.status == "WATCHLIST"
    insignificant = await service.process(
        watch.model_copy(update={"confidence_score": 77.0}), evaluated_at=NOW
    )
    assert insignificant.action == "DUPLICATE_SUPPRESSED"
    promoted = await service.process(
        base.model_copy(update={"confidence_score": 84.0}), evaluated_at=NOW
    )
    assert promoted.action == "UPDATED"
    assert promoted.record is not None and promoted.record.id == created.record.id
    assert promoted.record.status == "WAITING_ENTRY"
    assert promoted.record.maximum_confidence == 84.0
    assert promoted.record.minimum_confidence == 76.0
    promoted.record.expires_at = NOW - timedelta(seconds=1)
    assert await service.expire_due(NOW) == 1
    detail = await repo.get_signal(promoted.record.id)
    assert detail is not None and detail.status == "EXPIRED"
    assert len(detail.updates) == 1
    assert [event.new_status for event in detail.lifecycle_events] == [
        "DISCOVERED",
        "WATCHLIST",
        "WAITING_ENTRY",
        "EXPIRED",
    ]
    assert group_top([detail])[0]["signal"].id == detail.id


@pytest.mark.asyncio
async def test_expiry_accepts_sqlite_naive_timestamp_after_reload(tmp_path, bullish_market) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'expiry.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        created = await SignalService(SignalRepository(session), settings).process(
            DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW),
            evaluated_at=NOW,
        )
        assert created.record is not None
        created.record.expires_at = NOW - timedelta(seconds=1)
        await session.commit()
    async with database.sessions() as session:
        assert await SignalService(SignalRepository(session), settings).expire_due(NOW) == 1
    await database.close()


@pytest.mark.asyncio
async def test_scanner_failure_isolation_and_canonical_counts(repository, bullish_market) -> None:
    repo, settings = repository
    scanner = DirectionalScanner(SignalService(repo, settings), max_concurrency=2)

    async def provider(symbol: str):
        if symbol == "BADUSDT":
            raise RuntimeError("fixture failure")
        return bullish_market.model_copy(update={"symbol": symbol})

    result = await scanner.scan(["BTCUSDT", "BADUSDT"], provider)
    assert result.counts["analyses_completed"] == 1
    assert result.counts["errors"] == 1
    assert result.counts["actionable"] == (
        result.counts["actionable_now"] + result.counts["pending_entry"]
    )


@pytest.mark.asyncio
async def test_structural_rejection_invalidates_same_persisted_identity(
    repository, bullish_market
) -> None:
    repo, settings = repository
    service = SignalService(repo, settings)
    base = DirectionalSignalEngine().evaluate(bullish_market, evaluated_at=NOW)
    created = await service.process(base, evaluated_at=NOW)
    rejected = base.model_copy(
        update={
            "classification": SignalClassification.NO_TRADE,
            "rejection_reasons": ("failed breakout invalidated structure",),
        }
    )
    outcome = await service.process(rejected, evaluated_at=NOW)
    assert outcome.action == "INVALIDATED"
    assert outcome.record is not None and created.record is not None
    assert outcome.record.id == created.record.id
    assert outcome.record.status == "INVALIDATED"


def test_count_consistency_regression_for_previous_four_vs_five_report(bullish_market) -> None:
    signal = (
        DirectionalSignalEngine()
        .evaluate(bullish_market, evaluated_at=NOW)
        .model_copy(update={"classification": SignalClassification.STRONG_SETUP})
    )
    signals = tuple(ServiceOutcome(signal, None, "NO_TRADE") for _ in range(5))
    summary = ScanSummary(
        started_at=NOW, completed_at=NOW, markets_scanned=4, symbols_scanned=20, outcomes=signals
    )
    returned = [
        item
        for item in summary.outcomes
        if item.signal.classification is SignalClassification.STRONG_SETUP
    ]
    assert summary.counts["STRONG_SETUP"] == len(returned) == 5
