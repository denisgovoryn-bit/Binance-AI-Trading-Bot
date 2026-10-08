"""Async repository for pre-entry signals."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.signal_models import (
    SignalLifecycleEventRecord,
    SignalMonitoringEventRecord,
    SignalRecord,
    SignalUpdateRecord,
)
from app.signals.models import SignalStatus

ACTIVE_STATUSES = (
    "DISCOVERED",
    "WATCHLIST",
    "WAITING_ENTRY",
    "REVALIDATING",
    "ENTRY_CONFIRMED",
    "ACTIVE",
    "WEAKENING",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
)
MONITORABLE_STATUSES = (
    "WAITING_ENTRY",
    "REVALIDATING",
    "ENTRY_CONFIRMED",
    "ACTIVE",
    "WEAKENING",
    "TP1_REACHED",
    "TP2_REACHED",
    "TP3_REACHED",
)


class SignalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_signal(self, record: SignalRecord) -> SignalRecord:
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_signal(self, signal_id: str) -> SignalRecord | None:
        statement = self._detailed().where(SignalRecord.id == signal_id)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_active_signal_by_fingerprint(self, fingerprint: str) -> SignalRecord | None:
        statement = self._detailed().where(
            SignalRecord.fingerprint == fingerprint, SignalRecord.status.in_(ACTIVE_STATUSES)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_latest_by_fingerprint(self, fingerprint: str) -> SignalRecord | None:
        statement = (
            self._detailed()
            .where(SignalRecord.fingerprint == fingerprint)
            .order_by(desc(SignalRecord.created_at))
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_active_candidate(
        self, exchange: str, market: str, symbol: str, direction: str, timeframe: str
    ) -> SignalRecord | None:
        statement = (
            self._detailed()
            .where(
                SignalRecord.exchange == exchange,
                SignalRecord.market == market,
                SignalRecord.symbol == symbol,
                SignalRecord.direction == direction,
                SignalRecord.timeframe == timeframe,
                SignalRecord.status.in_(ACTIVE_STATUSES),
            )
            .order_by(desc(SignalRecord.created_at))
            .limit(1)
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def add_lifecycle_event(self, event: SignalLifecycleEventRecord) -> None:
        self.session.add(event)
        await self.session.flush()

    async def add_score_update(self, update: SignalUpdateRecord) -> None:
        self.session.add(update)
        await self.session.flush()

    async def list_signals(
        self,
        *,
        statuses: Sequence[str] | None = None,
        exchange: str | None = None,
        market: str | None = None,
        symbol: str | None = None,
        direction: str | None = None,
        timeframe: str | None = None,
        min_confidence: float | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[SignalRecord]:
        statement = self._detailed()
        if statuses:
            statement = statement.where(SignalRecord.status.in_(statuses))
        for column, value in (
            (SignalRecord.exchange, exchange),
            (SignalRecord.market, market),
            (SignalRecord.symbol, symbol),
            (SignalRecord.direction, direction),
            (SignalRecord.timeframe, timeframe),
        ):
            if value is not None:
                statement = statement.where(column == value.upper())
        if min_confidence is not None:
            statement = statement.where(SignalRecord.current_confidence >= min_confidence)
        result = await self.session.execute(
            statement.order_by(desc(SignalRecord.last_evaluated_at)).offset(offset).limit(limit)
        )
        return list(result.scalars().unique())

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        """End a read transaction so later queries observe commits from scanner sessions."""
        await self.session.rollback()

    async def recover_interrupted_revalidations(self, at: datetime) -> int:
        rows = await self.list_signals(statuses=[SignalStatus.REVALIDATING.value], limit=1000)
        for row in rows:
            row.status = SignalStatus.WAITING_ENTRY.value
            self.session.add(
                SignalLifecycleEventRecord(
                    signal=row,
                    previous_status=SignalStatus.REVALIDATING.value,
                    new_status=SignalStatus.WAITING_ENTRY.value,
                    reason="Interrupted revalidation restored after process restart",
                    at=at,
                )
            )
        if rows:
            await self.session.commit()
        return len(rows)

    @staticmethod
    def _detailed() -> Select[tuple[SignalRecord]]:
        return select(SignalRecord).options(
            selectinload(SignalRecord.lifecycle_events),
            selectinload(SignalRecord.updates),
            selectinload(SignalRecord.targets),
            selectinload(SignalRecord.monitoring_events),
        )

    async def add_monitoring_event(self, event: SignalMonitoringEventRecord) -> None:
        self.session.add(event)
        await self.session.flush()

    async def has_monitoring_event(self, event_key: str) -> bool:
        statement = select(SignalMonitoringEventRecord.id).where(
            SignalMonitoringEventRecord.event_key == event_key
        )
        return (await self.session.execute(statement)).scalar_one_or_none() is not None
