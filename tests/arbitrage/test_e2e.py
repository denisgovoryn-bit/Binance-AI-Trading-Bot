from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.arbitrage.models import ArbitrageEconomics
from app.arbitrage.notifications import ArbitrageNotificationService
from app.arbitrage.paper_service import PaperArbitrageService
from app.arbitrage.scanner import ArbitrageCandidate
from app.arbitrage.service import ArbitrageService
from app.config.settings import Settings
from app.db.repositories.arbitrage import ArbitrageRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.session import Database
from app.market.models import ExchangeName, MarketType
from app.portfolio.user_service import UserService

NOW = datetime(2026, 8, 9, tzinfo=UTC)
D = Decimal


@pytest.mark.asyncio
async def test_qualified_to_two_leg_close_and_notification_exactly_once(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/e2e.db",
        arbitrage_min_consecutive_updates=3,
        arbitrage_min_persistence_ms=300,
    )
    database = Database(settings)
    await database.create_schema()
    sent: list[int] = []

    async def sender(chat_id: int, _text: str) -> int:
        sent.append(chat_id)
        return 42

    async with database.sessions() as session:
        portfolio = PortfolioRepository(session)
        user, _ = await UserService(portfolio).get_or_create(123)
        account = await UserService(portfolio).complete_onboarding(user, "1000", ["SPOT"])
        economics = ArbitrageEconomics(
            target_notional=D("500"),
            executable_notional=D("500"),
            gross_spread_pct=D("0.50"),
            fees_pct=D("0.20"),
            slippage_pct=D("0"),
            safety_buffer_pct=D("0.02"),
            expected_net_edge_pct=D("0.28"),
            expected_net_pnl=D("1.40"),
            buy_vwap=D("100"),
            sell_vwap=D("100.5"),
            quantity=D("5"),
        )
        candidate = ArbitrageCandidate(
            "BTCUSDT",
            MarketType.SPOT,
            ExchangeName.BINANCE,
            ExchangeName.BYBIT,
            economics,
            D("10"),
            D("10"),
            D("0"),
        )
        service = ArbitrageService(ArbitrageRepository(session), settings)
        for offset in (0, 200, 400):
            opportunity, _ = await service.process(candidate, NOW + timedelta(milliseconds=offset))
        assert opportunity.status == "QUALIFIED"
        assert await PaperArbitrageService(session, settings).open_qualified(opportunity, NOW) == 1
        assert await PaperArbitrageService(session, settings).open_qualified(opportunity, NOW) == 0
        position = (await portfolio.list_arbitrage_positions(account.id))[0]
        assert await PaperArbitrageService(session, settings).close(
            position,
            buy_exit=D("100.2"),
            sell_exit=D("100.2"),
            fees=D("0.2"),
            funding_pnl=D("0.1"),
            at=NOW + timedelta(seconds=2),
            reason="SPREAD_CONVERGED",
            processing_key="arb-1:CLOSE",
        )
        assert not await PaperArbitrageService(session, settings).close(
            position,
            buy_exit=D("100.2"),
            sell_exit=D("100.2"),
            fees=D("0.2"),
            funding_pnl=D("0.1"),
            at=NOW + timedelta(seconds=3),
            reason="DUPLICATE",
            processing_key="arb-1:CLOSE",
        )
        notifier = ArbitrageNotificationService(portfolio, sender)
        assert await notifier.notify(opportunity, "QUALIFIED") == 1
        assert await notifier.notify(opportunity, "QUALIFIED") == 0
        assert sent == [123]
        assert position.status == "CLOSED" and position.realized_pnl > 0
    await database.close()
