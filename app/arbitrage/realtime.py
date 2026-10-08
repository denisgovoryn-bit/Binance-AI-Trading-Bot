"""WebSocket-driven book ingestion with independent subscription failures."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.arbitrage.liquidity import normalize_book
from app.arbitrage.scanner import ArbitrageCandidate, ArbitrageScanner
from app.exchanges.base import BaseExchange
from app.market.models import ExchangeName

CandidateHandler = Callable[[ArbitrageCandidate], Awaitable[None]]
logger = logging.getLogger(__name__)


class RealtimeArbitrageManager:
    def __init__(self, scanner: ArbitrageScanner, handler: CandidateHandler) -> None:
        self.scanner = scanner
        self.handler = handler
        self.events_processed = 0
        self.errors = 0
        self.last_event_at: datetime | None = None
        self.last_event_by_exchange: dict[ExchangeName, datetime] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._handler_lock = asyncio.Lock()

    def subscribe(self, exchange: BaseExchange, symbols: tuple[str, ...]) -> None:
        if symbols:
            self._tasks.append(asyncio.create_task(self._consume(exchange, symbols)))

    async def _consume(self, exchange: BaseExchange, symbols: tuple[str, ...]) -> None:
        try:
            async for raw in exchange.subscribe_orderbooks(symbols):
                received = datetime.now(UTC)
                self.events_processed += 1
                self.last_event_at = received
                self.last_event_by_exchange[raw.exchange] = received
                for candidate in self.scanner.update(
                    normalize_book(raw, received_at=received), now=received
                ):
                    try:
                        async with self._handler_lock:
                            await self.handler(candidate)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.errors += 1
                        logger.error(
                            "arbitrage_candidate_handler_failed",
                            extra={"error_type": type(exc).__name__, "error": str(exc)},
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # The adapter owns reconnect behavior; a terminal stream error is isolated here.
            self.errors += 1
            logger.error(
                "arbitrage_websocket_consumer_terminated",
                extra={"error_type": type(exc).__name__, "error": str(exc)},
            )

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    @property
    def failed_tasks(self) -> int:
        return sum(task.done() and not task.cancelled() for task in self._tasks)
