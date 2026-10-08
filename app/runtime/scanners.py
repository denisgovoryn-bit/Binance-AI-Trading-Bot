"""Bounded background directional and arbitrage scanner runtime."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from app.arbitrage.notifications import ArbitrageNotificationService
from app.arbitrage.realtime import RealtimeArbitrageManager
from app.arbitrage.scanner import ArbitrageCandidate, ArbitrageScanner
from app.arbitrage.service import ArbitrageService
from app.config.settings import Settings
from app.db.directional_models import DirectionalScanCycleRecord
from app.db.repositories.arbitrage import ArbitrageRepository
from app.db.repositories.directional_diagnostics import DirectionalDiagnosticsRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.exchanges.base import BaseExchange
from app.market.analysis import AnalysisService, MarketAnalysis
from app.market.models import ExchangeName, MarketType, Timeframe
from app.signals.scanner import DirectionalScanner
from app.signals.service import SignalService
from app.telegram.notifications import TelegramNotificationService
from app.universe.models import UniverseSnapshot
from app.universe.service import SymbolUniverseService

logger = logging.getLogger(__name__)
SendMessage = Callable[[int, str], Awaitable[int | None]]


@dataclass
class ScannerRuntimeState:
    universe_status: str = "NOT_STARTED"
    directional_status: str = "NOT_STARTED"
    arbitrage_status: str = "NOT_STARTED"
    directional_symbols: int = 0
    arbitrage_symbols: int = 0
    last_universe_refresh: datetime | None = None
    last_directional_scan: datetime | None = None
    last_directional_duration_ms: float | None = None
    latest_actionable: int = 0
    latest_pending: int = 0
    latest_watchlist: int = 0
    latest_no_trade: int = 0
    latest_arbitrage_candidates: int = 0
    latest_arbitrage_qualified: int = 0
    websocket_events_processed: int = 0
    arbitrage_last_event: datetime | None = None
    websocket_reconnects: int = 0
    binance_last_websocket_event: datetime | None = None
    bybit_last_websocket_event: datetime | None = None
    public_binance: str = "NOT_STARTED"
    public_bybit: str = "NOT_STARTED"

    def safe_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


class ScannerRuntime:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        exchanges: dict[tuple[ExchangeName, MarketType], BaseExchange],
        sender: SendMessage,
        after_directional_scan: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.exchanges = exchanges
        self.sender = sender
        self.after_directional_scan = after_directional_scan
        self.universe = SymbolUniverseService(settings, exchanges)
        self.state = ScannerRuntimeState()
        self._tasks: list[asyncio.Task[None]] = []
        self._arbitrage: RealtimeArbitrageManager | None = None
        self._arbitrage_failure_reported = False
        self._stopping = False
        self._started = False

    async def start(self) -> UniverseSnapshot:
        if self._started:
            raise RuntimeError("scanner runtime is already started")
        self._started = True
        self.state.universe_status = "STARTING"
        snapshot = await self.universe.refresh()
        self._apply_snapshot(snapshot)
        if self.settings.directional_scanner_enabled:
            self.state.directional_status = "RUNNING"
            self._tasks.append(asyncio.create_task(self._directional_loop(), name="directional"))
        else:
            self.state.directional_status = "DISABLED"
        if self.settings.arbitrage_scanner_enabled:
            await self._start_arbitrage(snapshot)
        else:
            self.state.arbitrage_status = "DISABLED"
        self._tasks.append(asyncio.create_task(self._refresh_loop(), name="universe-refresh"))
        return snapshot

    async def stop(self) -> None:
        self._stopping = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._arbitrage is not None:
            await self._arbitrage.close()
            self._arbitrage = None
        if self.state.directional_status != "DISABLED":
            self.state.directional_status = "STOPPED"
        if self.state.arbitrage_status != "DISABLED":
            self.state.arbitrage_status = "STOPPED"
        self._started = False

    def update_live_metrics(self) -> None:
        if self._arbitrage is not None:
            self.state.arbitrage_last_event = self._arbitrage.last_event_at
            self.state.binance_last_websocket_event = self._arbitrage.last_event_by_exchange.get(
                ExchangeName.BINANCE
            )
            self.state.bybit_last_websocket_event = self._arbitrage.last_event_by_exchange.get(
                ExchangeName.BYBIT
            )
            self.state.websocket_events_processed = self._arbitrage.events_processed
        self.state.websocket_reconnects = sum(
            getattr(getattr(adapter, "websocket", None), "reconnect_count", 0)
            for adapter in self.exchanges.values()
        )

    async def watchdog(self) -> None:
        """Restart unexpectedly terminated scanner loops or subscription consumers."""
        if self._stopping:
            return
        self._tasks = [task for task in self._tasks if not task.done()]
        directional = next((task for task in self._tasks if task.get_name() == "directional"), None)
        if self.settings.directional_scanner_enabled and (
            directional is None or directional.done()
        ):
            self.state.directional_status = "DEGRADED"
            self._tasks.append(asyncio.create_task(self._directional_loop(), name="directional"))
            logger.warning("Directional scanner watchdog restarted the scan loop")
        refresh = next(
            (task for task in self._tasks if task.get_name() == "universe-refresh"), None
        )
        if refresh is None or refresh.done():
            self._tasks.append(asyncio.create_task(self._refresh_loop(), name="universe-refresh"))
            logger.warning("Universe watchdog restarted the refresh loop")
        if (
            self._arbitrage is not None
            and self._arbitrage.failed_tasks
            and not self._arbitrage_failure_reported
        ):
            self.state.arbitrage_status = "DEGRADED"
            self._arbitrage_failure_reported = True
            logger.error(
                "Arbitrage WebSocket consumer terminated; adapter reconnect ownership retained"
            )

    async def scan_once(self) -> None:
        snapshot = self.universe.snapshot
        if snapshot is None:
            return
        started = perf_counter()
        started_at = datetime.now(UTC)
        cycle_id = str(uuid4())
        expected_by_market: dict[str, int] = {}
        for row in snapshot.directional:
            market_label = f"{row.exchange.value}:{row.market.value}"
            expected_by_market[market_label] = expected_by_market.get(market_label, 0) + 1
        async with self.database.sessions() as session:
            await DirectionalDiagnosticsRepository(session).create_cycle(
                DirectionalScanCycleRecord(
                    id=cycle_id,
                    started_at=started_at,
                    status="RUNNING",
                    expected_markets=len(snapshot.directional),
                    evaluated_markets=0,
                    failed_markets=0,
                    expected_by_market=expected_by_market,
                    errors=[],
                )
            )
        totals = {"actionable": 0, "pending": 0, "watchlist": 0, "no_trade": 0}
        evaluated = 0
        scan_errors: list[dict[str, str]] = []
        for key, adapter in self.exchanges.items():
            rows = [row for row in snapshot.directional if (row.exchange, row.market) == key]
            if not rows:
                continue
            async with self.database.sessions() as session:
                repository = SignalRepository(session)
                scanner = DirectionalScanner(
                    SignalService(repository, self.settings), self.settings.scanner_max_concurrency
                )
                analysis = AnalysisService.from_settings(self.settings)

                summary = await scanner.scan(
                    [row.symbol for row in rows],
                    self._analysis_provider(adapter, analysis),
                    cycle_id=cycle_id,
                )
                evaluated += len(summary.outcomes)
                scan_errors.extend(
                    {
                        "exchange": key[0].value,
                        "market": key[1].value,
                        "symbol": error.symbol,
                        "error_type": "MARKET_DATA",
                    }
                    for error in summary.errors
                )
                notifier = TelegramNotificationService(PortfolioRepository(session), self.sender)
                for outcome in summary.outcomes:
                    if outcome.action == "CREATED" and outcome.record is not None:
                        await notifier.notify_new_signal(outcome.record)
                totals["actionable"] += summary.counts["actionable_now"]
                totals["pending"] += summary.counts["pending_entry"]
                totals["watchlist"] += summary.counts["WATCHLIST"]
                totals["no_trade"] += summary.counts["NO_TRADE"]
        self.state.last_directional_scan = datetime.now(UTC)
        self.state.last_directional_duration_ms = (perf_counter() - started) * 1000
        self.state.latest_actionable = totals["actionable"]
        self.state.latest_pending = totals["pending"]
        self.state.latest_watchlist = totals["watchlist"]
        self.state.latest_no_trade = totals["no_trade"]
        self.state.directional_status = "RUNNING"
        async with self.database.sessions() as session:
            await DirectionalDiagnosticsRepository(session).finish_cycle(
                cycle_id,
                completed_at=self.state.last_directional_scan,
                evaluated=evaluated,
                failed=len(scan_errors),
                errors=scan_errors,
                duration_ms=self.state.last_directional_duration_ms,
            )
        if self.after_directional_scan is not None:
            await self.after_directional_scan()
        logger.info(
            "Directional scan complete: symbols=%d actionable=%d pending=%d "
            "watchlist=%d no_trade=%d duration_ms=%.2f",
            self.state.directional_symbols,
            self.state.latest_actionable,
            self.state.latest_pending,
            self.state.latest_watchlist,
            self.state.latest_no_trade,
            self.state.last_directional_duration_ms,
        )

    async def _directional_loop(self) -> None:
        delay = self.settings.directional_full_scan_minutes * 60
        while not self._stopping:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Directional scan cycle failed")
                self.state.directional_status = "DEGRADED"
            await asyncio.sleep(delay)

    async def _refresh_loop(self) -> None:
        delay = self.settings.symbol_universe_refresh_minutes * 60
        while not self._stopping:
            await asyncio.sleep(delay)
            try:
                snapshot = await self.universe.refresh()
                self._apply_snapshot(snapshot)
                if self.settings.arbitrage_scanner_enabled:
                    await self._start_arbitrage(snapshot)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state.universe_status = "DEGRADED"
                logger.warning("Universe refresh failed; retaining last valid snapshot")

    async def _start_arbitrage(self, snapshot: UniverseSnapshot) -> None:
        if self._arbitrage is not None:
            await self._arbitrage.close()
        scanner = ArbitrageScanner(self.settings)

        async def handler(candidate: ArbitrageCandidate) -> None:
            self.state.latest_arbitrage_candidates += 1
            async with self.database.sessions() as session:
                opportunity, _ = await ArbitrageService(
                    ArbitrageRepository(session), self.settings
                ).process(candidate, datetime.now(UTC))
                if opportunity.status == "QUALIFIED":
                    self.state.latest_arbitrage_qualified += 1
                    await ArbitrageNotificationService(
                        PortfolioRepository(session), self.sender
                    ).notify(opportunity, "QUALIFIED")

        manager = RealtimeArbitrageManager(scanner, handler)
        self._arbitrage_failure_reported = False
        limit = self.settings.arbitrage_runtime_max_symbols_per_market
        for market, symbols in (
            (MarketType.SPOT, snapshot.cross_exchange_spot[:limit]),
            (MarketType.FUTURES, snapshot.cross_exchange_futures[:limit]),
        ):
            for exchange in ExchangeName:
                adapter = self.exchanges.get((exchange, market))
                if adapter is not None and symbols:
                    manager.subscribe(adapter, symbols)
        self._arbitrage = manager
        self.state.arbitrage_symbols = len(snapshot.cross_exchange_spot[:limit]) + len(
            snapshot.cross_exchange_futures[:limit]
        )
        self.state.arbitrage_status = "RUNNING" if self.state.arbitrage_symbols else "IDLE"

    def _apply_snapshot(self, snapshot: UniverseSnapshot) -> None:
        self.state.universe_status = "DEGRADED" if snapshot.degraded_markets else "RUNNING"
        self.state.directional_symbols = len(snapshot.directional)
        self.state.last_universe_refresh = snapshot.refreshed_at
        degraded = set(snapshot.degraded_markets)
        self.state.public_binance = (
            "DEGRADED" if any(name.startswith("BINANCE") for name in degraded) else "CONNECTED"
        )
        self.state.public_bybit = (
            "DEGRADED" if any(name.startswith("BYBIT") for name in degraded) else "CONNECTED"
        )

    @staticmethod
    def _analysis_provider(
        adapter: BaseExchange, analysis: AnalysisService
    ) -> Callable[[str], Awaitable[MarketAnalysis]]:
        async def provider(symbol: str) -> MarketAnalysis:
            timeframes = (Timeframe.M15, Timeframe.H1, Timeframe.H4)
            evaluated_at = datetime.now(UTC)
            candles = await asyncio.gather(
                *(adapter.get_candles(symbol, timeframe, 250) for timeframe in timeframes)
            )
            completed = {
                timeframe: [row for row in rows if row.close_time <= evaluated_at]
                for timeframe, rows in zip(timeframes, candles, strict=True)
            }
            return analysis.analyze_market(completed)

        return provider
