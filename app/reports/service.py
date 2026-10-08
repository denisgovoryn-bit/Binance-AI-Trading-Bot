"""Daily, weekly, monthly, all-time and global paper reports."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.db.portfolio_models import PaperAccountRecord
from app.db.repositories.portfolio import PortfolioRepository
from app.reports.models import PerformanceReport
from app.reports.statistics import calculate_metrics


class ReportService:
    def __init__(self, repository: PortfolioRepository) -> None:
        self.repository = repository

    async def generate(
        self, account: PaperAccountRecord, report_type: str, end: datetime | None = None
    ) -> PerformanceReport:
        period_end = end or datetime.now(UTC)
        period_start = _start(report_type, period_end)
        all_positions = await self.repository.list_positions(account.id)
        positions = [x for x in all_positions if period_start <= _aware(x.opened_at) <= period_end]
        snapshots = sorted(
            (x for x in account.snapshots if period_start <= _aware(x.captured_at) <= period_end),
            key=lambda x: x.captured_at,
        )
        start_equity = snapshots[0].equity if snapshots else account.starting_balance
        end_equity = snapshots[-1].equity if snapshots else account.current_equity
        return PerformanceReport(
            report_type=report_type.upper(),
            period_start=period_start,
            period_end=period_end,
            generated_at=datetime.now(UTC),
            account_id=account.id,
            global_strategy=False,
            metrics=calculate_metrics(account, positions, start_equity, end_equity),
        )

    async def global_strategy(self) -> PerformanceReport:
        positions = await self.repository.all_positions()
        now = datetime.now(UTC)
        return PerformanceReport(
            report_type="ALL_TIME",
            period_start=min((_aware(x.opened_at) for x in positions), default=now),
            period_end=now,
            generated_at=now,
            account_id=None,
            global_strategy=True,
            metrics=calculate_metrics(
                None,
                positions,
                Decimal("0"),
                sum((x.realized_pnl for x in positions), Decimal("0")),
            ),
        )


def _start(report_type: str, end: datetime) -> datetime:
    kind = report_type.upper()
    if kind == "DAILY":
        return end.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "WEEKLY":
        day = end - timedelta(days=end.weekday())
        return day.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "MONTHLY":
        return end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return datetime.min.replace(tzinfo=UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
