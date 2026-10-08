"""Async aiogram application lifecycle."""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.accounts.factory import create_account_service
from app.accounts.service import AccountService
from app.config.settings import Settings
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import SignalRepository
from app.db.session import Database
from app.db.signal_models import SignalRecord
from app.exchanges.base import BaseExchange
from app.exchanges.binance.futures import BinanceFuturesExchange
from app.exchanges.binance.spot import BinanceSpotExchange
from app.exchanges.bybit.futures import BybitFuturesExchange
from app.exchanges.bybit.spot import BybitSpotExchange
from app.market.analysis import AnalysisService, MarketAnalysis
from app.market.models import ExchangeName, MarketType, Timeframe
from app.monitoring.manager import MonitoringManager
from app.monitoring.models import PriceEvent
from app.portfolio.paper_trade import PaperTradeService
from app.runtime.health import HealthSupervisor, SystemStatus
from app.runtime.scanners import ScannerRuntime, ScannerRuntimeState
from app.services.phase5_fanout import Phase5EventFanout
from app.signals.config import SignalConfig
from app.signals.engine import DirectionalSignalEngine
from app.signals.models import DirectionalSignal
from app.telegram.notifications import TelegramNotificationService
from app.telegram.router import build_router

logger = logging.getLogger(__name__)


def create_dispatcher(
    database: Database,
    settings: Settings,
    account_service: AccountService | None = None,
    scanner_state: ScannerRuntimeState | None = None,
    system_status: SystemStatus | None = None,
) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(
        build_router(database, settings, account_service, scanner_state, system_status)
    )
    return dispatcher


async def run_bot(database: Database, settings: Settings) -> None:
    if not settings.telegram_enabled:
        return
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required when Telegram is enabled")
    bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    account_service = create_account_service(settings)
    exchanges: dict[tuple[ExchangeName, MarketType], BaseExchange] = {
        (ExchangeName.BINANCE, MarketType.SPOT): BinanceSpotExchange(),
        (ExchangeName.BINANCE, MarketType.FUTURES): BinanceFuturesExchange(),
        (ExchangeName.BYBIT, MarketType.SPOT): BybitSpotExchange(),
        (ExchangeName.BYBIT, MarketType.FUTURES): BybitFuturesExchange(),
    }
    manager: MonitoringManager | None = None
    scanner_runtime: ScannerRuntime | None = None
    health_supervisor: HealthSupervisor | None = None
    try:
        identity = await bot.get_me()
        logger.info("Telegram connected as @%s", identity.username)
        accounts = await account_service.get_accounts(force=True)
        logger.info(
            "Private accounts: %s",
            ", ".join(f"{row.exchange}={row.status.value}" for row in accounts) or "disabled",
        )
        async with database.sessions() as session:
            portfolio = PortfolioRepository(session)

            async def sender(chat_id: int, text: str) -> int:
                sent = await bot.send_message(chat_id, text)
                return sent.message_id

            analysis_service = AnalysisService.from_settings(settings)
            signal_engine = DirectionalSignalEngine(SignalConfig.from_settings(settings))

            async def analyze_symbol(
                adapter: BaseExchange, symbol: str, evaluated_at: datetime
            ) -> MarketAnalysis:
                timeframes = (Timeframe.M15, Timeframe.H1, Timeframe.H4)
                candles = await asyncio.gather(
                    *(adapter.get_candles(symbol, timeframe, 250) for timeframe in timeframes)
                )
                completed = {
                    timeframe: [row for row in rows if row.close_time <= evaluated_at]
                    for timeframe, rows in zip(timeframes, candles, strict=True)
                }
                return analysis_service.analyze_market(completed)

            async def revalidate_entry(
                record: SignalRecord, event: PriceEvent
            ) -> DirectionalSignal:
                adapter = exchanges[(ExchangeName(record.exchange), MarketType(record.market))]
                market = await analyze_symbol(adapter, record.symbol, event.received_at)
                btc_context = (
                    None
                    if record.symbol == "BTCUSDT"
                    else await analyze_symbol(adapter, "BTCUSDT", event.received_at)
                )
                return signal_engine.evaluate(
                    market,
                    btc_context=btc_context,
                    evaluated_at=event.received_at,
                )

            manager = MonitoringManager(
                SignalRepository(session),
                settings,
                exchanges,
                event_consumer=Phase5EventFanout(
                    PaperTradeService(portfolio, settings),
                    TelegramNotificationService(portfolio, sender),
                ),
                entry_revalidation=revalidate_entry,
            )
            if settings.monitoring_enabled:
                await manager.sync_subscriptions()
            scanner_runtime = ScannerRuntime(
                settings, database, exchanges, sender, manager.sync_subscriptions
            )
            snapshot = await scanner_runtime.start()

            async def telegram_probe() -> bool:
                await bot.get_me()
                return True

            health_supervisor = HealthSupervisor(
                settings,
                database,
                scanner_runtime,
                manager,
                account_service,
                telegram_probe,
                sender,
            )
            await health_supervisor.check_once()
            health_supervisor.start()
            health = manager.health()
            logger.info(
                "Realtime monitoring: %s (%d subscriptions)",
                health["websocket_connection_state"],
                health["active_subscriptions"],
            )
            logger.info(
                "Symbol universe: %d directional, %d Spot arbitrage, %d Futures arbitrage",
                len(snapshot.directional),
                len(snapshot.cross_exchange_spot),
                len(snapshot.cross_exchange_futures),
            )
            logger.info("Telegram polling starting; trading execution DISABLED")
            await create_dispatcher(
                database,
                settings,
                account_service,
                scanner_runtime.state,
                health_supervisor.status,
            ).start_polling(bot)
    finally:
        if health_supervisor is not None:
            await health_supervisor.stop()
        if scanner_runtime is not None:
            await scanner_runtime.stop()
        if manager is not None:
            await manager.stop()
        await asyncio.gather(*(exchange.close() for exchange in exchanges.values()))
        await account_service.close()
        await bot.session.close()
        logger.info("Telegram polling stopped cleanly")
