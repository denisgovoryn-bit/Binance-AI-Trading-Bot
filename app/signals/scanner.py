"""Failure-isolated, concurrency-bounded directional scan orchestration."""

import asyncio
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from pydantic import BaseModel

from app.market.analysis import MarketAnalysis
from app.signals.models import SignalClassification
from app.signals.service import ServiceOutcome, SignalService

AnalysisProvider = Callable[[str], Awaitable[MarketAnalysis]]


class ScanError(BaseModel):
    symbol: str
    message: str


class ScanSummary(BaseModel):
    started_at: datetime
    completed_at: datetime
    markets_scanned: int
    symbols_scanned: int
    outcomes: tuple[ServiceOutcome, ...]
    errors: tuple[ScanError, ...] = ()
    expired_signals: int = 0

    model_config = {"arbitrary_types_allowed": True}

    @property
    def duration_ms(self) -> float:
        return (self.completed_at - self.started_at).total_seconds() * 1000

    @property
    def counts(self) -> dict[str, int]:
        classifications = Counter(item.signal.classification.value for item in self.outcomes)
        actions = Counter(item.action for item in self.outcomes)
        strong = [
            item.signal
            for item in self.outcomes
            if item.signal.classification
            in {SignalClassification.STRONG_SETUP, SignalClassification.VERY_STRONG_SETUP}
        ]
        actionable_now = sum(
            signal.supporting_metrics.get("entry_zone_state") == "ACTIONABLE_NOW"
            for signal in strong
        )
        return {
            "analyses_completed": len(self.outcomes),
            "candidates": len(self.outcomes),
            "NO_TRADE": classifications[SignalClassification.NO_TRADE.value],
            "WATCHLIST": classifications[SignalClassification.WATCHLIST.value],
            "STRONG_SETUP": classifications[SignalClassification.STRONG_SETUP.value],
            "VERY_STRONG_SETUP": classifications[SignalClassification.VERY_STRONG_SETUP.value],
            "actionable": classifications[SignalClassification.STRONG_SETUP.value]
            + classifications[SignalClassification.VERY_STRONG_SETUP.value],
            "actionable_now": actionable_now,
            "pending_entry": len(strong) - actionable_now,
            "created": actions["CREATED"],
            "updated": actions["UPDATED"],
            "duplicates_suppressed": actions["DUPLICATE_SUPPRESSED"],
            "invalidated": actions["INVALIDATED"],
            "expired": self.expired_signals,
            "rejected": actions["REJECTED"],
            "errors": len(self.errors),
        }


class DirectionalScanner:
    def __init__(self, service: SignalService, max_concurrency: int = 4) -> None:
        self.service = service
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def scan(
        self,
        symbols: Sequence[str],
        provider: AnalysisProvider,
        *,
        cycle_id: str | None = None,
    ) -> ScanSummary:
        started = datetime.now(UTC)
        expired = await self.service.expire_due(started)
        cache: dict[str, MarketAnalysis] = {}

        async def one(symbol: str) -> tuple[str, MarketAnalysis | Exception]:
            try:
                async with self.semaphore:
                    cache[symbol] = await provider(symbol)
                return symbol, cache[symbol]
            except Exception as exc:
                return symbol, exc

        fetched = await asyncio.gather(*(one(symbol) for symbol in symbols))
        btc = cache.get("BTCUSDT")
        outcomes: list[ServiceOutcome] = []
        errors: list[ScanError] = []
        for symbol, result in fetched:
            if isinstance(result, Exception):
                errors.append(ScanError(symbol=symbol, message=str(result)))
            else:
                outcomes.append(
                    await self.service.evaluate(result, btc_context=btc, cycle_id=cycle_id)
                )
        return ScanSummary(
            started_at=started,
            completed_at=datetime.now(UTC),
            markets_scanned=1,
            symbols_scanned=len(symbols),
            outcomes=tuple(outcomes),
            errors=tuple(errors),
            expired_signals=expired,
        )


def benchmark_summary(outcomes: Sequence[ServiceOutcome], repetitions: int = 1000) -> float:
    now = datetime.now(UTC)
    start = time.perf_counter()
    total = 0
    for _ in range(repetitions):
        total += ScanSummary(
            started_at=now,
            completed_at=now,
            markets_scanned=1,
            symbols_scanned=len(outcomes),
            outcomes=tuple(outcomes),
        ).counts["candidates"]
    assert total >= 0
    return (time.perf_counter() - start) * 1000
