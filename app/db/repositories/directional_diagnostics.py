"""Persistence operations for immutable directional decision audits."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.directional_models import DirectionalEvaluationRecord, DirectionalScanCycleRecord


class DirectionalDiagnosticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_cycle(self, row: DirectionalScanCycleRecord) -> None:
        self.session.add(row)
        await self.session.commit()

    async def finish_cycle(
        self,
        cycle_id: str,
        *,
        completed_at: datetime,
        evaluated: int,
        failed: int,
        errors: list[dict[str, str]],
        duration_ms: float,
    ) -> None:
        row = await self.session.get(DirectionalScanCycleRecord, cycle_id)
        if row is None:
            return
        row.completed_at = completed_at
        row.evaluated_markets = evaluated
        row.failed_markets = failed
        row.errors = errors
        row.duration_ms = duration_ms
        row.status = "COMPLETED" if failed == 0 else "PARTIAL"
        await self.session.commit()

    async def add_evaluations(self, rows: list[DirectionalEvaluationRecord]) -> None:
        self.session.add_all(rows)
        await self.session.flush()

    async def cycles_since(self, cutoff: datetime) -> list[DirectionalScanCycleRecord]:
        result = await self.session.execute(
            select(DirectionalScanCycleRecord)
            .where(DirectionalScanCycleRecord.started_at >= cutoff)
            .order_by(DirectionalScanCycleRecord.started_at)
        )
        return list(result.scalars())

    async def evaluations_since(self, cutoff: datetime) -> list[DirectionalEvaluationRecord]:
        result = await self.session.execute(
            select(DirectionalEvaluationRecord)
            .where(DirectionalEvaluationRecord.evaluated_at >= cutoff)
            .order_by(DirectionalEvaluationRecord.evaluated_at.desc())
        )
        return list(result.scalars())
