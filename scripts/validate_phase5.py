"""Deterministic Phase 5 event→paper→report→Telegram validation and benchmarks."""

import asyncio
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from aiogram import Bot

from app.config.settings import Settings
from app.db.portfolio_models import PaperAccountRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.db.signal_models import SignalRecord, SignalTargetRecord
from app.monitoring.models import PriceEvent
from app.monitoring.signal_monitor import SignalMonitor
from app.portfolio.paper_trade import PaperTradeService
from app.portfolio.user_service import UserService
from app.reports.service import ReportService
from app.telegram.formatting import format_portfolio, format_report
from app.telegram.notifications import TelegramNotificationService


def fixture_signal(symbol: str = "BTCUSDT") -> SignalRecord:
    now = datetime.now(UTC)
    record = SignalRecord(
        id=str(uuid4()),
        fingerprint=str(uuid4()).replace("-", ""),
        correlation_group_key=f"{symbol}:LONG:FUTURES:15m",
        exchange="BINANCE",
        market="FUTURES",
        symbol=symbol,
        direction="LONG",
        setup_type="LONG_SETUP",
        timeframe="15m",
        classification="STRONG_SETUP",
        status="WAITING_ENTRY",
        created_at=now,
        analysis_timestamp=now,
        last_evaluated_at=now,
        initial_confidence=87,
        current_confidence=87,
        maximum_confidence=87,
        minimum_confidence=87,
        entry_low=99,
        entry_high=101,
        ideal_entry=100,
        invalidation_price=95,
        original_invalidation_price=95,
        monitoring_degraded=False,
        weakening_reasons=[],
        payload={
            "entry_zone": {"low": 99, "high": 101},
            "invalidation": {"price": 95},
            "targets": [
                {"name": "TP1", "price": 105, "r_multiple": 1},
                {"name": "TP2", "price": 110, "r_multiple": 2},
                {"name": "TP3", "price": 115, "r_multiple": 3},
            ],
            "paper_exit_plan": [
                {"target_name": "TP1", "percentage": 30},
                {"target_name": "TP2", "percentage": 30},
                {"target_name": "TP3", "percentage": 40},
            ],
            "reasons": ["deterministic fixture"],
            "warnings": [],
        },
    )
    record.targets = [
        SignalTargetRecord(
            ordinal=index,
            name=f"TP{index}",
            price=price,
            source="FIXTURE",
            reason="deterministic target",
            r_multiple=float(index),
        )
        for index, price in enumerate((105, 110, 115), 1)
    ]
    record.lifecycle_events = []
    record.updates = []
    record.monitoring_events = []
    return record


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="phase5-") as directory:
        settings = Settings(database_url=f"sqlite+aiosqlite:///{Path(directory) / 'phase5.db'}")
        database = Database(settings)
        await database.create_schema()
        deliveries: list[tuple[int, str]] = []

        async def sender(chat_id: int, text: str) -> int:
            deliveries.append((chat_id, text))
            return len(deliveries)

        async with database.sessions() as session:
            portfolios, signals = PortfolioRepository(session), SignalRepository(session)
            users = UserService(portfolios)
            accounts: list[PaperAccountRecord] = []
            for index in range(100):
                user, _ = await users.get_or_create(10_000 + index)
                accounts.append(await users.complete_onboarding(user, "1000"))
            signal = fixture_signal()
            await signals.create_signal(signal)
            await signals.commit()
            monitor = SignalMonitor(signals, settings)
            paper = PaperTradeService(portfolios, settings)
            notifier = TelegramNotificationService(portfolios, sender)
            now = signal.created_at
            started = time.perf_counter()
            await monitor.process_price(
                signal,
                PriceEvent(
                    symbol=signal.symbol,
                    last=100,
                    bid=100,
                    ask=100,
                    exchange_timestamp=now,
                    received_at=now,
                ),
            )
            entry_event = next(
                x for x in signal.monitoring_events if x.event_type == "ENTRY_CONFIRMED"
            )
            await paper.process(signal, entry_event)
            fanout_ms = (time.perf_counter() - started) * 1000
            notify_started = time.perf_counter()
            sent = await notifier.notify_event(signal, entry_event)
            notification_ms = (time.perf_counter() - notify_started) * 1000
            await notifier.notify_event(signal, entry_event)
            handled = {entry_event.id}
            for offset, price in enumerate((105, 110, 115), 1):
                await monitor.process_price(
                    signal,
                    PriceEvent(
                        symbol=signal.symbol,
                        last=price,
                        bid=price,
                        ask=price,
                        exchange_timestamp=now + timedelta(seconds=offset),
                        received_at=now + timedelta(seconds=offset),
                    ),
                )
                for event in signal.monitoring_events:
                    if event.id not in handled:
                        await paper.process(signal, event)
                        handled.add(event.id)
            stop_signal = fixture_signal("STOPUSDT")
            await signals.create_signal(stop_signal)
            await signals.commit()
            await monitor.process_price(
                stop_signal,
                PriceEvent(
                    symbol=stop_signal.symbol,
                    last=100,
                    bid=100,
                    ask=100,
                    exchange_timestamp=now + timedelta(seconds=10),
                    received_at=now + timedelta(seconds=10),
                ),
            )
            stop_entry = next(
                x for x in stop_signal.monitoring_events if x.event_type == "ENTRY_CONFIRMED"
            )
            await paper.process(stop_signal, stop_entry)
            await monitor.process_price(
                stop_signal,
                PriceEvent(
                    symbol=stop_signal.symbol,
                    last=95,
                    bid=95,
                    ask=95,
                    exchange_timestamp=now + timedelta(seconds=11),
                    received_at=now + timedelta(seconds=11),
                ),
            )
            stop_event = next(
                x for x in stop_signal.monitoring_events if x.event_type == "STOP_REACHED"
            )
            await paper.process(stop_signal, stop_event)
            account = accounts[0]
            positions = await portfolios.list_positions(account.id)
            report_started = time.perf_counter()
            report = await ReportService(portfolios).generate(account, "ALL_TIME")
            report_ms = (time.perf_counter() - report_started) * 1000
            format_started = time.perf_counter()
            rendered = format_portfolio(account, positions) + format_report(report)
            format_ms = (time.perf_counter() - format_started) * 1000
            position = next(x for x in positions if x.symbol == "BTCUSDT")
            print(
                f"E2E LONG: status={position.status} fills={[x.fill_type for x in position.fills]} "
                f"equity={account.current_equity} pnl={position.realized_pnl} fees={position.fees}"
            )
            stopped = next(x for x in positions if x.symbol == "STOPUSDT")
            print(
                f"E2E STOP: status={stopped.status} fills={[x.fill_type for x in stopped.fills]} "
                f"pnl={stopped.realized_pnl}"
            )
            print(f"Notifications exactly once: users={sent}, deliveries={len(deliveries)}")
            print(
                f"100-user paper fanout: {fanout_ms:.2f}ms; "
                f"notification fanout+dedup: {notification_ms:.2f}ms"
            )
            print(
                f"Report generation: {report_ms:.2f}ms; Telegram formatting: "
                f"{format_ms:.3f}ms; chars={len(rendered)}"
            )
        await database.close()
        if settings.telegram_bot_token:
            bot = Bot(settings.telegram_bot_token)
            try:
                identity = await bot.get_me()
                print(f"Live Telegram startup: OK bot_id={identity.id}")
            finally:
                await bot.session.close()
        else:
            print("Live Telegram smoke: SKIPPED (TELEGRAM_BOT_TOKEN not configured)")


if __name__ == "__main__":
    asyncio.run(main())
