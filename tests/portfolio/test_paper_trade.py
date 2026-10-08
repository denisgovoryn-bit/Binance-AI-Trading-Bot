from datetime import timedelta
from decimal import Decimal

import pytest

from app.config.settings import Settings
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor
from app.portfolio.paper_trade import PaperTradeService
from app.portfolio.pnl import gross_pnl, return_percentage, unrealized_pnl
from app.portfolio.user_service import UserService
from app.signals.analytics import pending_metrics
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from tests.signals.conftest import NOW, make_market


def test_decimal_pnl_signs_and_returns() -> None:
    quantity = Decimal("2")
    assert gross_pnl("BUY", Decimal("100"), Decimal("110"), quantity) == Decimal("20.00000000")
    assert gross_pnl("LONG", Decimal("100"), Decimal("90"), quantity) == Decimal("-20.00000000")
    assert gross_pnl("SHORT", Decimal("100"), Decimal("90"), quantity) == Decimal("20.00000000")
    assert gross_pnl("SHORT", Decimal("100"), Decimal("110"), quantity) == Decimal("-20.00000000")
    assert unrealized_pnl("LONG", Decimal("100"), Decimal("105"), quantity) == Decimal(
        "10.00000000"
    )
    assert return_percentage(Decimal("10"), Decimal("100")) == Decimal("10.0000")


@pytest.mark.asyncio
async def test_event_fanout_partial_targets_idempotency_and_user_isolation(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'paper.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        portfolio_repo = PortfolioRepository(session)
        signal_repo = SignalRepository(session)
        users = UserService(portfolio_repo)
        alice, _ = await users.get_or_create(1, "alice")
        bob, _ = await users.get_or_create(2, "bob")
        alice_account = await users.complete_onboarding(alice, "1000")
        bob_account = await users.complete_onboarding(bob, "2000")
        signal = DirectionalSignalEngine().evaluate(make_market(bullish=True), evaluated_at=NOW)
        outcome = await SignalService(signal_repo, settings).process(signal, evaluated_at=NOW)
        assert outcome.record is not None and signal.targets
        monitor = SignalMonitor(signal_repo, settings)
        entry = signal.ideal_entry or Decimal("100")
        await monitor.process_price(
            outcome.record,
            PriceEvent(
                symbol=signal.symbol,
                last=float(entry),
                bid=float(entry),
                ask=float(entry),
                exchange_timestamp=NOW,
                received_at=NOW,
            ),
        )
        await monitor.complete_entry_revalidation(
            outcome.record,
            signal,
            PriceEvent(
                symbol=signal.symbol,
                last=float(entry),
                bid=float(entry),
                ask=float(entry),
                exchange_timestamp=NOW,
                received_at=NOW,
            ),
        )
        paper = PaperTradeService(portfolio_repo, settings)
        entry_event = next(
            x for x in outcome.record.monitoring_events if x.event_type == "ENTRY_CONFIRMED"
        )
        assert await paper.process(outcome.record, entry_event) == 2
        assert await paper.process(outcome.record, entry_event) == 0
        alice_position = await portfolio_repo.position_for_signal(
            alice_account.id, outcome.record.id
        )
        bob_position = await portfolio_repo.position_for_signal(bob_account.id, outcome.record.id)
        assert alice_position is not None and bob_position is not None
        metrics = pending_metrics([outcome.record], [alice_position, bob_position])
        assert metrics["pending_setups_created"] == 1
        assert metrics["entry_zones_reached"] == 1
        assert metrics["paper_entries_opened"] == 1
        assert bob_position.simulated_notional == alice_position.simulated_notional * 2
        assert alice_position.simulated_fill_price > Decimal(str(entry))
        assert len(alice_position.fills) == 1

        await monitor.process_price(
            outcome.record,
            PriceEvent(
                symbol=signal.symbol,
                last=signal.targets[0].price,
                bid=signal.targets[0].price,
                ask=signal.targets[0].price,
                exchange_timestamp=NOW + timedelta(seconds=1),
                received_at=NOW + timedelta(seconds=1),
            ),
        )
        tp1 = next(x for x in outcome.record.monitoring_events if x.event_type == "TP1_REACHED")
        assert await paper.process(outcome.record, tp1) == 2
        assert await paper.process(outcome.record, tp1) == 0
        assert alice_position.remaining_quantity < alice_position.initial_quantity
        assert alice_position.realized_pnl > -alice_position.fees

        await monitor.process_price(
            outcome.record,
            PriceEvent(
                symbol=signal.symbol,
                last=signal.targets[-1].price,
                bid=signal.targets[-1].price,
                ask=signal.targets[-1].price,
                exchange_timestamp=NOW + timedelta(seconds=2),
                received_at=NOW + timedelta(seconds=2),
            ),
        )
        for event in outcome.record.monitoring_events:
            if event.event_type in {"TP2_REACHED", "TP3_REACHED", "SIGNAL_COMPLETED"}:
                await paper.process(outcome.record, event)
        assert alice_position.remaining_quantity == 0
        assert alice_position.status == "COMPLETED"
        assert alice_account.current_equity > alice_account.starting_balance
        assert bob_account.current_equity > alice_account.current_equity
        assert sum(
            fill.allocation_pct for fill in alice_position.fills if fill.fill_type != "ENTRY"
        ) == Decimal("100")
    await database.close()


@pytest.mark.asyncio
async def test_stop_after_entry_and_mark_to_market(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'stop.db'}")
    database = Database(settings)
    await database.create_schema()
    async with database.sessions() as session:
        portfolio_repo, signal_repo = PortfolioRepository(session), SignalRepository(session)
        user, _ = await UserService(portfolio_repo).get_or_create(3)
        account = await UserService(portfolio_repo).complete_onboarding(user, "1000")
        signal = DirectionalSignalEngine().evaluate(make_market(bullish=False), evaluated_at=NOW)
        outcome = await SignalService(signal_repo, settings).process(signal, evaluated_at=NOW)
        assert outcome.record is not None and signal.invalidation is not None
        monitor = SignalMonitor(signal_repo, settings)
        entry = signal.ideal_entry or 100
        await monitor.process_price(
            outcome.record,
            PriceEvent(
                symbol=signal.symbol,
                last=entry,
                bid=entry,
                ask=entry,
                exchange_timestamp=NOW,
                received_at=NOW,
            ),
        )
        await monitor.complete_entry_revalidation(
            outcome.record,
            signal,
            PriceEvent(
                symbol=signal.symbol,
                last=entry,
                bid=entry,
                ask=entry,
                exchange_timestamp=NOW,
                received_at=NOW,
            ),
        )
        paper = PaperTradeService(portfolio_repo, settings)
        await paper.process(
            outcome.record,
            next(x for x in outcome.record.monitoring_events if x.event_type == "ENTRY_CONFIRMED"),
        )
        position = await portfolio_repo.position_for_signal(account.id, outcome.record.id)
        assert position is not None
        await paper.mark_to_market(
            account, {position.symbol: position.simulated_fill_price - Decimal("2")}
        )
        assert account.unrealized_pnl > 0  # SHORT benefits from lower prices.
        await monitor.process_price(
            outcome.record,
            PriceEvent(
                symbol=signal.symbol,
                last=signal.invalidation.price,
                bid=signal.invalidation.price,
                ask=signal.invalidation.price,
                exchange_timestamp=NOW + timedelta(seconds=1),
                received_at=NOW + timedelta(seconds=1),
            ),
        )
        stop = next(x for x in outcome.record.monitoring_events if x.event_type == "STOP_REACHED")
        assert await paper.process(outcome.record, stop) == 1
        assert position.status == "STOPPED" and position.remaining_quantity == 0
        assert position.realized_pnl < 0 and account.current_equity < account.starting_balance
    await database.close()
