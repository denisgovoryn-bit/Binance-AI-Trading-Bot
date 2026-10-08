"""Async arbitrage persistence queries."""

from collections.abc import Sequence

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.arbitrage_models import ArbitrageOpportunityRecord

ACTIVE_ARBITRAGE_STATUSES = ("DETECTED", "PERSISTING", "QUALIFIED", "WEAKENING", "PAPER_ACTIVE")


class ArbitrageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, opportunity_id: str) -> ArbitrageOpportunityRecord | None:
        return (
            await self.session.execute(
                self._detail().where(ArbitrageOpportunityRecord.id == opportunity_id)
            )
        ).scalar_one_or_none()

    async def active_by_fingerprint(self, fingerprint: str) -> ArbitrageOpportunityRecord | None:
        statement = (
            self._detail()
            .where(
                ArbitrageOpportunityRecord.fingerprint == fingerprint,
                ArbitrageOpportunityRecord.status.in_(ACTIVE_ARBITRAGE_STATUSES),
            )
            .order_by(desc(ArbitrageOpportunityRecord.detected_at))
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def add(self, record: ArbitrageOpportunityRecord) -> None:
        self.session.add(record)
        await self.session.flush()

    async def list(
        self,
        *,
        statuses: Sequence[str] | None = None,
        symbol: str | None = None,
        arbitrage_type: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ArbitrageOpportunityRecord]:
        statement = self._detail()
        if statuses:
            statement = statement.where(ArbitrageOpportunityRecord.status.in_(statuses))
        if symbol:
            statement = statement.where(ArbitrageOpportunityRecord.symbol == symbol.upper())
        if arbitrage_type:
            statement = statement.where(
                ArbitrageOpportunityRecord.arbitrage_type == arbitrage_type.upper()
            )
        result = await self.session.execute(
            statement.order_by(desc(ArbitrageOpportunityRecord.last_updated_at))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().unique())

    async def commit(self) -> None:
        await self.session.commit()

    @staticmethod
    def _detail() -> Select[tuple[ArbitrageOpportunityRecord]]:
        return select(ArbitrageOpportunityRecord).options(
            selectinload(ArbitrageOpportunityRecord.events),
            selectinload(ArbitrageOpportunityRecord.updates),
        )
