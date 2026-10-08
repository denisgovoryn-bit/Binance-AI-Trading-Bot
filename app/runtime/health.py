"""24/7 runtime heartbeat, watchdogs, and deduplicated admin alerts."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.accounts.models import AccountConnectionStatus
from app.accounts.service import AccountService
from app.config.settings import Settings
from app.db.repositories.arbitrage import ArbitrageRepository
from app.db.repositories.signals import ACTIVE_STATUSES, SignalRepository
from app.db.session import Database
from app.market.models import ExchangeName
from app.monitoring.manager import MonitoringManager
from app.runtime.scanners import ScannerRuntime

logger = logging.getLogger(__name__)
Probe = Callable[[], Awaitable[bool]]
AdminSender = Callable[[int, str], Awaitable[int | None]]


@dataclass
class Incident:
    opened_at: datetime
    last_alert_at: datetime
    message: str


@dataclass
class SystemStatus:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    telegram: str = "STARTING"
    database: str = "STARTING"
    binance_public: str = "STARTING"
    binance_public_rest: str = "STARTING"
    binance_public_websocket: str = "STARTING"
    binance_private: str = "DISABLED"
    bybit_public: str = "STARTING"
    bybit_public_rest: str = "STARTING"
    bybit_public_websocket: str = "STARTING"
    bybit_private: str = "STARTING"
    directional_scanner: str = "STARTING"
    arbitrage_scanner: str = "STARTING"
    realtime_monitoring: str = "STARTING"
    universe_size: int = 0
    last_directional_scan: datetime | None = None
    last_universe_refresh: datetime | None = None
    last_market_event: datetime | None = None
    websocket_reconnects: int = 0
    active_signals: int = 0
    active_arbitrage_opportunities: int = 0
    trading_execution: str = "DISABLED"

    @property
    def uptime(self) -> timedelta:
        return datetime.now(UTC) - self.started_at

    def telegram_text(self) -> str:
        uptime = int(self.uptime.total_seconds())
        return (
            "<b>SYSTEM STATUS</b>\n\n"
            f"Uptime: {uptime // 86400}d {(uptime % 86400) // 3600}h "
            f"{(uptime % 3600) // 60}m\n"
            f"Telegram: {self.telegram}\nDatabase: {self.database}\n"
            f"Binance public: {self.binance_public}\n"
            f"  REST: {self.binance_public_rest}\n"
            f"  WebSocket: {self.binance_public_websocket}\n"
            f"Binance private: {self.binance_private}\n"
            f"Bybit public: {self.bybit_public}\n"
            f"  REST: {self.bybit_public_rest}\n"
            f"  WebSocket: {self.bybit_public_websocket}\n"
            f"Bybit private: {self.bybit_private}\n"
            f"Directional scanner: {self.directional_scanner}\n"
            f"Arbitrage scanner: {self.arbitrage_scanner}\n"
            f"Realtime monitoring: {self.realtime_monitoring}\n"
            f"Universe size: {self.universe_size}\n"
            f"Last directional scan: {self.last_directional_scan or 'never'}\n"
            f"Last universe refresh: {self.last_universe_refresh or 'never'}\n"
            f"Last market event: {self.last_market_event or 'never'}\n"
            f"WebSocket reconnects: {self.websocket_reconnects}\n"
            f"Active signals: {self.active_signals}\n"
            f"Active arbitrage opportunities: {self.active_arbitrage_opportunities}\n\n"
            "Trading Execution: 🔒 DISABLED"
        )


class HealthSupervisor:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        scanners: ScannerRuntime,
        monitoring: MonitoringManager,
        accounts: AccountService,
        telegram_probe: Probe,
        admin_sender: AdminSender,
    ) -> None:
        self.settings = settings
        self.database = database
        self.scanners = scanners
        self.monitoring = monitoring
        self.accounts = accounts
        self.telegram_probe = telegram_probe
        self.admin_sender = admin_sender
        self.status = SystemStatus()
        self.incidents: dict[str, Incident] = {}
        self.last_notifications: dict[str, datetime] = {}
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="runtime-health-supervisor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def check_once(self) -> SystemStatus:
        now = datetime.now(UTC)
        await self.scanners.watchdog()
        if self.settings.monitoring_enabled:
            await self.monitoring.sync_subscriptions()
        self.scanners.update_live_metrics()
        scanner = self.scanners.state
        monitor = self.monitoring.health()
        telegram_ok, database_ok = await asyncio.gather(self._safe_probe(), self.database.healthy())
        bybit = await self.accounts.get_account("BYBIT")
        active_signal_count = 0
        active_opportunity_count = 0
        if database_ok:
            async with self.database.sessions() as session:
                signals = await SignalRepository(session).list_signals(
                    statuses=ACTIVE_STATUSES, limit=1000
                )
                opportunities = await ArbitrageRepository(session).list(
                    statuses=("QUALIFIED", "PAPER_ACTIVE"), limit=1000
                )
                active_signal_count = len(signals)
                active_opportunity_count = len(opportunities)
        directional = self._directional_health(now)
        arbitrage = self._arbitrage_health(now)
        self.status.updated_at = now
        self.status.telegram = "CONNECTED" if telegram_ok else "UNAVAILABLE"
        self.status.database = "CONNECTED" if database_ok else "UNAVAILABLE"
        self.status.binance_public_rest = scanner.public_binance
        self.status.binance_public_websocket = self._websocket_health(ExchangeName.BINANCE, now)
        self.status.binance_public = self._aggregate_public_health(
            self.status.binance_public_rest, self.status.binance_public_websocket
        )
        self.status.binance_private = "DISABLED"
        self.status.bybit_public_rest = scanner.public_bybit
        self.status.bybit_public_websocket = self._websocket_health(ExchangeName.BYBIT, now)
        self.status.bybit_public = self._aggregate_public_health(
            self.status.bybit_public_rest, self.status.bybit_public_websocket
        )
        self.status.bybit_private = bybit.status.value
        self.status.directional_scanner = directional
        self.status.arbitrage_scanner = arbitrage
        self.status.realtime_monitoring = str(monitor["websocket_connection_state"])
        self.status.universe_size = scanner.directional_symbols
        self.status.last_directional_scan = scanner.last_directional_scan
        self.status.last_universe_refresh = scanner.last_universe_refresh
        self.status.last_market_event = (
            monitor["last_market_event_timestamp"]
            if isinstance(monitor["last_market_event_timestamp"], datetime)
            else scanner.arbitrage_last_event
        )
        monitor_reconnects = monitor["reconnect_count"]
        self.status.websocket_reconnects = scanner.websocket_reconnects + (
            monitor_reconnects if isinstance(monitor_reconnects, int) else 0
        )
        self.status.active_signals = active_signal_count
        self.status.active_arbitrage_opportunities = active_opportunity_count
        await self.evaluate_incidents()
        logger.info(
            "runtime_heartbeat telegram=%s database=%s binance=%s bybit=%s "
            "bybit_private=%s directional=%s arbitrage=%s monitoring=%s "
            "universe=%d websocket_events=%d reconnects=%d",
            self.status.telegram,
            self.status.database,
            self.status.binance_public,
            self.status.bybit_public,
            self.status.bybit_private,
            directional,
            arbitrage,
            self.status.realtime_monitoring,
            self.status.universe_size,
            scanner.websocket_events_processed,
            self.status.websocket_reconnects,
        )
        return self.status

    async def _run(self) -> None:
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("runtime health check failed")
            await asyncio.sleep(self.settings.runtime_heartbeat_seconds)

    async def _safe_probe(self) -> bool:
        try:
            return await self.telegram_probe()
        except Exception:
            return False

    def _directional_health(self, now: datetime) -> str:
        state = self.scanners.state
        if state.directional_status not in {"RUNNING", "STARTING"}:
            return state.directional_status
        if state.last_directional_scan is None:
            grace = timedelta(minutes=self.settings.directional_full_scan_minutes * 2)
            return "STARTING" if now - self.status.started_at < grace else "DEGRADED"
        stale_after = timedelta(
            minutes=self.settings.directional_full_scan_minutes
            * self.settings.runtime_scanner_stale_multiplier
        )
        return "DEGRADED" if now - state.last_directional_scan > stale_after else "RUNNING"

    def _arbitrage_health(self, now: datetime) -> str:
        state = self.scanners.state
        if state.arbitrage_status != "RUNNING":
            return state.arbitrage_status
        if state.arbitrage_last_event is None:
            grace = timedelta(seconds=self.settings.runtime_websocket_stale_seconds)
            return "STARTING" if now - self.status.started_at < grace else "DEGRADED"
        return (
            "DEGRADED"
            if (now - state.arbitrage_last_event).total_seconds()
            > self.settings.runtime_websocket_stale_seconds
            else "RUNNING"
        )

    def _websocket_health(self, exchange: ExchangeName, now: datetime) -> str:
        state = self.scanners.state
        if state.arbitrage_status != "RUNNING":
            return state.arbitrage_status
        last_event = (
            state.binance_last_websocket_event
            if exchange is ExchangeName.BINANCE
            else state.bybit_last_websocket_event
        )
        if last_event is None:
            return (
                "CONNECTED"
                if now - self.status.started_at
                < timedelta(seconds=self.settings.runtime_websocket_stale_seconds)
                else "DEGRADED"
            )
        return (
            "DEGRADED"
            if (now - last_event).total_seconds() > self.settings.runtime_websocket_stale_seconds
            else "CONNECTED"
        )

    def _public_health(self, exchange: ExchangeName, now: datetime) -> str:
        """Backward-compatible aggregate used by older callers and tests."""
        state = self.scanners.state
        rest = state.public_binance if exchange is ExchangeName.BINANCE else state.public_bybit
        return self._aggregate_public_health(rest, self._websocket_health(exchange, now))

    @staticmethod
    def _aggregate_public_health(rest: str, websocket: str) -> str:
        if rest == "CONNECTED" and websocket == "CONNECTED":
            return "CONNECTED"
        if rest == "NOT_STARTED" and websocket == "NOT_STARTED":
            return "NOT_STARTED"
        return "DEGRADED"

    async def evaluate_incidents(self) -> None:
        checks = {
            "telegram": (self.status.telegram, "Telegram polling unavailable"),
            "database": (self.status.database, "Database unavailable"),
            "binance_public": (self.status.binance_public, "Binance public data unavailable"),
            "bybit_public": (self.status.bybit_public, "Bybit public WebSocket unavailable"),
            "bybit_private": (self.status.bybit_private, "Bybit read-only account unavailable"),
            "directional": (self.status.directional_scanner, "Directional scanner stalled"),
            "arbitrage": (self.status.arbitrage_scanner, "Arbitrage scanner degraded"),
        }
        healthy = {
            "CONNECTED",
            "RUNNING",
            "IDLE",
            "STARTING",
            AccountConnectionStatus.READ_ONLY_PERMISSION_CONFIRMED.value,
            AccountConnectionStatus.CONNECTED_READ_ONLY.value,
        }
        now = datetime.now(UTC)
        for key, (state, message) in checks.items():
            incident = self.incidents.get(key)
            if state not in healthy and incident is None:
                self.incidents[key] = Incident(now, now, message)
                await self._alert(
                    key,
                    "🚨 <b>SYSTEM ALERT</b>\n\n"
                    f"{message}.\n\nDirectional scanner: {self.status.directional_scanner}\n"
                    f"Arbitrage scanner: {self.status.arbitrage_scanner}\n\n"
                    "Automatic reconnect: IN PROGRESS",
                )
            elif state in healthy and incident is not None:
                downtime = now - incident.opened_at
                self.incidents.pop(key, None)
                await self._alert(
                    key,
                    "✅ <b>SYSTEM RECOVERED</b>\n\n"
                    f"{incident.message}.\nDowntime: {int(downtime.total_seconds())} seconds.",
                    bypass_cooldown=True,
                )

    async def _alert(self, key: str, text: str, *, bypass_cooldown: bool = False) -> None:
        now = datetime.now(UTC)
        last = self.last_notifications.get(key)
        if (
            not bypass_cooldown
            and last is not None
            and (now - last).total_seconds() < self.settings.runtime_alert_cooldown_seconds
        ):
            return
        self.last_notifications[key] = now
        for admin_id in self.settings.telegram_admin_ids:
            try:
                await self.admin_sender(admin_id, text)
            except Exception:
                logger.warning("admin health alert delivery failed")
