"""Telegram-user onboarding and explicit paper-account reset."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.db.portfolio_models import PaperAccountRecord, TelegramUserRecord, UserPreferencesRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.portfolio.models import PaperProfile, PaperTradeMode, parse_balance


class UserService:
    def __init__(self, repository: PortfolioRepository) -> None:
        self.repository = repository

    async def get_or_create(
        self,
        telegram_user_id: int,
        username: str | None = None,
        first_name: str | None = None,
        language_code: str = "en",
    ) -> tuple[TelegramUserRecord, bool]:
        existing = await self.repository.get_user_by_telegram_id(telegram_user_id)
        if existing:
            return existing, False
        now = datetime.now(UTC)
        user = TelegramUserRecord(
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
            language_code=language_code,
            timezone="UTC",
            created_at=now,
            updated_at=now,
            onboarding_completed=False,
        )
        user.preferences = UserPreferencesRecord(
            enabled_exchanges=["BINANCE", "BYBIT"],
            enabled_markets=["SPOT", "FUTURES"],
            minimum_confidence=Decimal("80"),
            selected_symbols=[],
            selected_timeframes=[],
            receive_signal_notifications=True,
            receive_monitoring_updates=True,
            receive_daily_reports=False,
            receive_weekly_reports=True,
            receive_monthly_reports=True,
            paper_profile=PaperProfile.BALANCED.value,
            paper_trade_mode=PaperTradeMode.AUTO_PAPER.value,
        )
        try:
            await self.repository.add_user(user)
            await self.repository.commit()
            return user, True
        except IntegrityError:
            # Telegram may deliver queued duplicate /start updates concurrently.
            await self.repository.session.rollback()
            concurrent = await self.repository.get_user_by_telegram_id(telegram_user_id)
            if concurrent is None:
                raise
            return concurrent, False

    async def complete_onboarding(
        self, user: TelegramUserRecord, balance_text: str, markets: list[str] | None = None
    ) -> PaperAccountRecord:
        amount = parse_balance(balance_text)
        if await self.repository.active_account(user.id):
            raise ValueError("paper account already exists")
        now = datetime.now(UTC)
        account = PaperAccountRecord(
            user_id=user.id,
            base_currency="USDT",
            starting_balance=amount,
            current_cash=amount,
            current_equity=amount,
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            fees=Decimal("0"),
            simulated_slippage=Decimal("0"),
            peak_equity=amount,
            current_drawdown=Decimal("0"),
            max_drawdown=Decimal("0"),
            active=True,
            created_at=now,
        )
        await self.repository.add_account(account)
        if markets is not None:
            user.preferences.enabled_markets = markets
        user.onboarding_completed = True
        user.updated_at = now
        await self.repository.commit()
        return account

    async def reset_account(
        self, user: TelegramUserRecord, balance_text: str
    ) -> PaperAccountRecord:
        old = await self.repository.active_account(user.id)
        now = datetime.now(UTC)
        if old:
            old.active = False
            old.archived_at = now
        user.onboarding_completed = False
        await self.repository.commit()
        return await self.complete_onboarding(user, balance_text)
