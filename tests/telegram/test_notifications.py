from datetime import UTC, datetime, timedelta

import pytest

from app.config.settings import Settings
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor
from app.portfolio.user_service import UserService
from app.signals.engine import DirectionalSignalEngine
from app.signals.service import SignalService
from app.telegram.notifications import (
    MONITORING_NOTIFICATIONS,
    TelegramNotificationService,
    format_event,
)
from app.telegram.scheduler import report_due
from tests.signals.conftest import NOW, make_market


@pytest.mark.asyncio
async def test_notification_delivery_is_persistent_and_idempotent(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}")
    database = Database(settings)
    await database.create_schema()
    delivered: list[tuple[int, str]] = []

    async def sender(chat_id: int, text: str) -> int:
        delivered.append((chat_id, text))
        return 42

    async with database.sessions() as session:
        portfolio_repo, signal_repo = PortfolioRepository(session), SignalRepository(session)
        user, _ = await UserService(portfolio_repo).get_or_create(1234)
        await UserService(portfolio_repo).complete_onboarding(user, "1000")
        signal = DirectionalSignalEngine().evaluate(make_market(bullish=True), evaluated_at=NOW)
        outcome = await SignalService(signal_repo, settings).process(signal, evaluated_at=NOW)
        assert outcome.record is not None
        notifier = TelegramNotificationService(portfolio_repo, sender)
        assert await notifier.notify_new_signal(outcome.record) == 1
        assert await notifier.notify_new_signal(outcome.record) == 0
        entry = signal.ideal_entry or 100
        monitor = SignalMonitor(signal_repo, settings)
        price_event = PriceEvent(
            symbol=signal.symbol,
            last=entry,
            bid=entry,
            ask=entry,
            exchange_timestamp=NOW,
            received_at=NOW,
        )
        await monitor.process_price(
            outcome.record,
            price_event,
        )
        await monitor.complete_entry_revalidation(outcome.record, signal, price_event)
        event = next(
            x for x in outcome.record.monitoring_events if x.event_type == "ENTRY_CONFIRMED"
        )
        assert await notifier.notify_event(outcome.record, event) == 1
        assert await notifier.notify_event(outcome.record, event) == 0
        assert len(delivered) == 2 and all("No real order" in text for _, text in delivered)
        assert "ENTRY CONFIRMED" in format_event(outcome.record, event)
    await database.close()


def test_timezone_aware_report_schedule() -> None:
    preferences = type(
        "Preferences",
        (),
        {
            "receive_daily_reports": True,
            "receive_weekly_reports": True,
            "receive_monthly_reports": True,
        },
    )()
    user = type("User", (), {"timezone": "Europe/Kiev", "preferences": preferences})()
    monday_local_nine = datetime(2026, 8, 3, 6, 0, tzinfo=UTC)
    assert report_due(user, "DAILY", monday_local_nine)
    assert report_due(user, "WEEKLY", monday_local_nine)
    assert not report_due(user, "MONTHLY", monday_local_nine)
    assert not report_due(user, "DAILY", monday_local_nine + timedelta(minutes=1))


def test_all_required_monitoring_notification_routes_are_enabled() -> None:
    assert {
        "ENTRY_CONFIRMED",
        "SIGNAL_WEAKENING",
        "SIGNAL_RECOVERED",
        "TP1_REACHED",
        "TP2_REACHED",
        "TP3_REACHED",
        "STOP_REACHED",
        "SIGNAL_COMPLETED",
    } <= MONITORING_NOTIFICATIONS
