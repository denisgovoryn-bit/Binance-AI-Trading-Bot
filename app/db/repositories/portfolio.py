"""Async persistence boundary for users and paper portfolios."""

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.arbitrage_models import PaperArbitragePositionRecord
from app.db.portfolio_models import (
    PaperAccountRecord,
    PaperFillRecord,
    PaperPositionRecord,
    PortfolioSnapshotRecord,
    TelegramUserRecord,
    UserNotificationDeliveryRecord,
)


class PortfolioRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_user_by_telegram_id(self, telegram_id: int) -> TelegramUserRecord | None:
        stmt = (
            select(TelegramUserRecord)
            .options(
                selectinload(TelegramUserRecord.preferences),
                selectinload(TelegramUserRecord.accounts),
            )
            .where(TelegramUserRecord.telegram_user_id == telegram_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_user(self, user_id: int) -> TelegramUserRecord | None:
        stmt = (
            select(TelegramUserRecord)
            .options(
                selectinload(TelegramUserRecord.preferences),
                selectinload(TelegramUserRecord.accounts),
            )
            .where(TelegramUserRecord.id == user_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_user(self, user: TelegramUserRecord) -> TelegramUserRecord:
        self.session.add(user)
        await self.session.flush()
        return user

    async def active_account(self, user_id: int) -> PaperAccountRecord | None:
        stmt = (
            select(PaperAccountRecord)
            .options(
                selectinload(PaperAccountRecord.positions),
                selectinload(PaperAccountRecord.snapshots),
            )
            .where(PaperAccountRecord.user_id == user_id, PaperAccountRecord.active.is_(True))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_account(self, account: PaperAccountRecord) -> PaperAccountRecord:
        self.session.add(account)
        await self.session.flush()
        return account

    async def eligible_users(self) -> list[TelegramUserRecord]:
        stmt = (
            select(TelegramUserRecord)
            .options(
                selectinload(TelegramUserRecord.preferences),
                selectinload(TelegramUserRecord.accounts),
            )
            .where(TelegramUserRecord.onboarding_completed.is_(True))
        )
        return list((await self.session.execute(stmt)).scalars().unique())

    async def position_for_signal(
        self, account_id: int, signal_id: str
    ) -> PaperPositionRecord | None:
        stmt = (
            select(PaperPositionRecord)
            .options(selectinload(PaperPositionRecord.fills))
            .where(
                PaperPositionRecord.account_id == account_id,
                PaperPositionRecord.signal_id == signal_id,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_positions(
        self, account_id: int, status: str | None = None
    ) -> list[PaperPositionRecord]:
        stmt = (
            select(PaperPositionRecord)
            .options(selectinload(PaperPositionRecord.fills))
            .where(PaperPositionRecord.account_id == account_id)
        )
        if status:
            stmt = stmt.where(PaperPositionRecord.status == status)
        rows = await self.session.execute(stmt.order_by(desc(PaperPositionRecord.opened_at)))
        return list(rows.scalars().unique())

    async def all_positions(self) -> list[PaperPositionRecord]:
        rows = await self.session.execute(
            select(PaperPositionRecord)
            .options(selectinload(PaperPositionRecord.fills))
            .order_by(desc(PaperPositionRecord.opened_at))
        )
        return list(rows.scalars().unique())

    async def add_position(self, position: PaperPositionRecord) -> PaperPositionRecord:
        self.session.add(position)
        await self.session.flush()
        return position

    async def has_fill(self, position_id: int, event_id: int) -> bool:
        stmt = select(PaperFillRecord.id).where(
            PaperFillRecord.position_id == position_id, PaperFillRecord.event_id == event_id
        )
        return (await self.session.execute(stmt)).scalar_one_or_none() is not None

    async def add_fill(self, fill: PaperFillRecord) -> None:
        self.session.add(fill)
        await self.session.flush()

    async def add_snapshot(self, snapshot: PortfolioSnapshotRecord) -> None:
        self.session.add(snapshot)
        await self.session.flush()

    async def notification_delivery(
        self, user_id: int, key: str
    ) -> UserNotificationDeliveryRecord | None:
        stmt = select(UserNotificationDeliveryRecord).where(
            UserNotificationDeliveryRecord.user_id == user_id,
            UserNotificationDeliveryRecord.delivery_key == key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def add_notification(self, delivery: UserNotificationDeliveryRecord) -> None:
        self.session.add(delivery)
        await self.session.flush()

    async def commit(self) -> None:
        await self.session.commit()

    async def list_arbitrage_positions(
        self, account_id: int, status: str | None = None
    ) -> list[PaperArbitragePositionRecord]:
        stmt = (
            select(PaperArbitragePositionRecord)
            .options(
                selectinload(PaperArbitragePositionRecord.legs),
                selectinload(PaperArbitragePositionRecord.fills),
            )
            .where(PaperArbitragePositionRecord.account_id == account_id)
        )
        if status:
            stmt = stmt.where(PaperArbitragePositionRecord.status == status)
        rows = await self.session.execute(
            stmt.order_by(desc(PaperArbitragePositionRecord.opened_at))
        )
        return list(rows.scalars().unique())
