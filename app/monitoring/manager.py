"""Subscription lifecycle and routing for monitorable persisted signals."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter

from app.config.settings import Settings
from app.db.repositories.signals import MONITORABLE_STATUSES, SignalRepository
from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord
from app.exchanges.base import BaseExchange
from app.market.models import ExchangeName, MarketType, Ticker
from app.monitoring.events import MonitoringEventType
from app.monitoring.models import PriceEvent, PriceSource
from app.monitoring.signal_monitor import SignalMonitor
from app.signals.models import DirectionalSignal, SignalDirection, SignalStatus

logger = logging.getLogger(__name__)

AnalysisRefresh = Callable[[SignalRecord], Awaitable[None]]
EventConsumer = Callable[[SignalRecord, SignalMonitoringEventRecord], Awaitable[None]]
EntryRevalidation = Callable[[SignalRecord, PriceEvent], Awaitable[DirectionalSignal]]


class MonitoringManager:
    """Subscribe only to required venue/symbol streams and restore them after failures."""

    def __init__(
        self,
        repository: SignalRepository,
        settings: Settings,
        exchanges: dict[tuple[ExchangeName, MarketType], BaseExchange],
        analysis_refresh: AnalysisRefresh | None = None,
        event_consumer: EventConsumer | None = None,
        entry_revalidation: EntryRevalidation | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.exchanges = exchanges
        self.analysis_refresh = analysis_refresh
        self.event_consumer = event_consumer
        self.entry_revalidation = entry_revalidation
        self.monitor = SignalMonitor(repository, settings)
        self.tasks: dict[tuple[str, str, str], asyncio.Task[None]] = {}
        self.semaphore = asyncio.Semaphore(settings.monitor_max_concurrency)
        self.monitored_signal_count = 0
        self.last_market_event: datetime | None = None
        self.last_analysis_refresh: datetime | None = None
        self.reconnect_count = 0
        self.entry_events = self.target_events = self.stop_events = self.weakening_events = 0
        self.processed_events = 0
        self.total_latency_ms = 0.0
        self._stopping = False
        self._revalidation_recovery_done = False
        self._repository_lock = asyncio.Lock()

    async def sync_subscriptions(self) -> None:
        async with self._repository_lock:
            # The manager session lives for the process lifetime. End any prior SQLite
            # read snapshot before looking for signals committed by scanner sessions.
            await self.repository.rollback()
            now = datetime.now(UTC)
            if not self._revalidation_recovery_done:
                await self.repository.recover_interrupted_revalidations(now)
                self._revalidation_recovery_done = True
            records = await self.repository.list_signals(statuses=MONITORABLE_STATUSES, limit=1000)
            await self.repository.commit()
        self.monitored_signal_count = len(records)
        wanted = {(x.exchange, x.market, x.symbol) for x in records}
        for key in list(self.tasks):
            if key not in wanted:
                self.tasks.pop(key).cancel()
        for key in wanted:
            if key not in self.tasks or self.tasks[key].done():
                self.tasks[key] = asyncio.create_task(
                    self._run_symbol(*key), name="monitor:" + ":".join(key)
                )

    async def route_ticker(
        self, ticker: Ticker, *, source: PriceSource = PriceSource.TICKER
    ) -> None:
        async with self._repository_lock:
            await self._route_ticker(ticker, source=source)

    async def _route_ticker(
        self, ticker: Ticker, *, source: PriceSource = PriceSource.TICKER
    ) -> None:
        started = perf_counter()
        records = await self.repository.list_signals(
            statuses=MONITORABLE_STATUSES,
            exchange=ticker.exchange.value,
            market=ticker.market.value,
            symbol=ticker.symbol,
            limit=100,
        )
        event = PriceEvent(
            symbol=ticker.symbol,
            last=float(ticker.last_price),
            bid=float(ticker.bid_price),
            ask=float(ticker.ask_price),
            exchange_timestamp=ticker.timestamp,
            received_at=datetime.now(UTC),
            source=source,
        )
        for record in records:
            before = {event.id for event in record.monitoring_events}
            async with self.semaphore:
                result = await self.monitor.process_price(record, event)
                if MonitoringEventType.REVALIDATION_STARTED.value in result.events:
                    if self.entry_revalidation is None:
                        await self.monitor.defer_entry_revalidation(
                            record, event, "Fresh analysis provider is unavailable"
                        )
                    else:
                        try:
                            revalidated = await self.entry_revalidation(record, event)
                            await self.monitor.complete_entry_revalidation(
                                record, revalidated, event
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            await self.monitor.defer_entry_revalidation(
                                record,
                                event,
                                "Fresh analysis failed; no paper entry was opened",
                            )
            if self.event_consumer is not None:
                for created in record.monitoring_events:
                    if created.id not in before:
                        await self.event_consumer(record, created)
            created_types = [
                created.event_type
                for created in record.monitoring_events
                if created.id not in before
            ]
            self.entry_events += created_types.count("ENTRY_CONFIRMED")
            self.target_events += sum(name.startswith("TP") for name in created_types)
            self.stop_events += created_types.count("STOP_REACHED")
            self.weakening_events += created_types.count("SIGNAL_WEAKENING")
            if SignalStatus(record.status) in {
                SignalStatus.WAITING_ENTRY,
                SignalStatus.REVALIDATING,
                SignalStatus.INVALIDATED,
                SignalStatus.ACTIVE,
            }:
                executable = (
                    float(ticker.ask_price)
                    if SignalDirection(record.direction).bullish
                    else float(ticker.bid_price)
                )
                logger.debug(
                    "pending_entry_audit signal_id=%s exchange=%s market=%s symbol=%s "
                    "direction=%s status=%s entry_low=%s entry_high=%s last=%s bid=%s "
                    "ask=%s executable=%s quote_side=%s inside_entry_zone=%s "
                    "market_event_at=%s monitor_attached=true revalidation_started=%s "
                    "revalidation_result=%s revalidation_reason=%s",
                    record.id,
                    record.exchange,
                    record.market,
                    record.symbol,
                    record.direction,
                    record.status,
                    record.entry_low,
                    record.entry_high,
                    ticker.last_price,
                    ticker.bid_price,
                    ticker.ask_price,
                    executable,
                    "ASK" if SignalDirection(record.direction).bullish else "BID",
                    SignalMonitor._inside_entry(record, executable),
                    ticker.timestamp.isoformat(),
                    record.revalidation_started_at is not None,
                    "PASSED"
                    if record.status == SignalStatus.ACTIVE.value
                    else (
                        "FAILED" if record.status == SignalStatus.INVALIDATED.value else "NOT_RUN"
                    ),
                    record.revalidation_reason,
                )
        self.last_market_event = ticker.timestamp
        self.processed_events += 1
        self.total_latency_ms += (perf_counter() - started) * 1000

    async def reconcile(self, exchange: ExchangeName, market: MarketType, symbol: str) -> None:
        adapter = self.exchanges[(exchange, market)]
        await self.route_ticker(
            await adapter.get_ticker(symbol), source=PriceSource.RECOVERY_SNAPSHOT
        )

    async def refresh(self, record: SignalRecord) -> None:
        if self.analysis_refresh is not None:
            await self.analysis_refresh(record)
            self.last_analysis_refresh = datetime.now(UTC)

    async def stop(self) -> None:
        self._stopping = True
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    def health(self) -> dict[str, object]:
        return {
            "enabled": self.settings.monitoring_enabled,
            "active_subscriptions": sum(not task.done() for task in self.tasks.values()),
            "monitored_signals": self.monitored_signal_count,
            "websocket_connection_state": "RUNNING" if self.tasks else "IDLE",
            "last_market_event_timestamp": self.last_market_event,
            "last_analysis_refresh": self.last_analysis_refresh,
            "stale_feed_count": 0,
            "entry_events": self.entry_events,
            "target_events": self.target_events,
            "stop_events": self.stop_events,
            "weakening_events": self.weakening_events,
            "reconnect_count": self.reconnect_count,
            "average_event_processing_latency_ms": (
                self.total_latency_ms / self.processed_events if self.processed_events else 0.0
            ),
        }

    async def _run_symbol(self, exchange_value: str, market_value: str, symbol: str) -> None:
        exchange, market = ExchangeName(exchange_value), MarketType(market_value)
        adapter = self.exchanges[(exchange, market)]
        delay = self.settings.monitor_reconnect_initial_seconds
        while not self._stopping:
            try:
                await self.reconcile(exchange, market, symbol)
                async for ticker in adapter.subscribe_ticker(symbol):
                    await self.route_ticker(ticker)
                raise ConnectionError("ticker stream ended")
            except asyncio.CancelledError:
                raise
            except Exception:
                self.reconnect_count += 1
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.settings.monitor_reconnect_max_seconds)
