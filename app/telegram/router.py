"""Aiogram command routing; handlers delegate all domain work to services."""

from datetime import UTC, datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, User

from app.accounts.formatting import format_account, format_connections, format_positions
from app.accounts.service import AccountService
from app.config.settings import Settings
from app.db.repositories.arbitrage import ArbitrageRepository
from app.db.repositories.portfolio import PortfolioRepository
from app.db.repositories.signals import ACTIVE_STATUSES, SignalRepository
from app.db.session import Database
from app.portfolio.models import parse_balance
from app.portfolio.user_service import UserService
from app.reports.service import ReportService
from app.runtime.health import SystemStatus
from app.runtime.scanners import ScannerRuntimeState
from app.signals.diagnostics import directional_diagnostics
from app.signals.ranking import group_top
from app.signals.service import SignalService
from app.telegram.formatting import (
    format_arbitrage_top,
    format_directional_stats,
    format_near_misses,
    format_paper_arbitrage,
    format_pending,
    format_portfolio,
    format_position,
    format_report,
    format_signal,
    format_top,
    onboarding_summary,
    welcome_text,
)
from app.telegram.keyboards import market_keyboard, reset_keyboard
from app.telegram.states import Onboarding, ResetPaper


def build_router(
    database: Database,
    settings: Settings,
    account_service: AccountService | None = None,
    scanner_state: ScannerRuntimeState | None = None,
    system_status: SystemStatus | None = None,
) -> Router:
    router = Router(name="primary-telegram-ui")

    @router.message(Command("start"))
    async def start(message: Message, state: FSMContext) -> None:
        identity = _identity(message)
        async with database.sessions() as session:
            user, created = await UserService(PortfolioRepository(session)).get_or_create(
                identity.id,
                identity.username,
                identity.first_name,
                identity.language_code or settings.telegram_default_language,
            )
        if not created and user.onboarding_completed:
            await message.answer("Welcome back. Use /top, /active, or /portfolio.")
            return
        await state.set_state(Onboarding.balance)
        await message.answer(welcome_text(identity.first_name))

    @router.message(Onboarding.balance)
    async def onboarding_balance(message: Message, state: FSMContext) -> None:
        try:
            amount = parse_balance(message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.update_data(balance=str(amount))
        await state.set_state(Onboarding.markets)
        await message.answer(
            "Which markets should this paper account follow?", reply_markup=market_keyboard()
        )

    @router.callback_query(Onboarding.markets, F.data.startswith("markets:"))
    async def onboarding_markets(callback: CallbackQuery, state: FSMContext) -> None:
        assert callback.from_user is not None
        choice = (callback.data or "markets:BOTH").split(":", 1)[1]
        markets = ["SPOT", "FUTURES"] if choice == "BOTH" else [choice]
        data = await state.get_data()
        async with database.sessions() as session:
            repo = PortfolioRepository(session)
            service = UserService(repo)
            user, _ = await service.get_or_create(callback.from_user.id)
            account = await service.complete_onboarding(user, str(data["balance"]), markets)
        await state.clear()
        await callback.answer()
        if callback.message:
            await callback.message.answer(onboarding_summary(account, markets))

    @router.message(Command("top"))
    async def top(message: Message) -> None:
        async with database.sessions() as session:
            rows = await SignalRepository(session).list_signals(
                statuses=["WAITING_ENTRY"], min_confidence=settings.minimum_confidence, limit=100
            )
            text = format_top(group_top(rows, 5))
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("active"))
    async def active(message: Message) -> None:
        statuses = [
            x for x in ACTIVE_STATUSES if x not in {"DISCOVERED", "WATCHLIST", "WAITING_ENTRY"}
        ]
        async with database.sessions() as session:
            rows = await SignalRepository(session).list_signals(statuses=statuses, limit=20)
        text = (
            "\n\n".join(format_signal(row) for row in rows)
            if rows
            else "No active monitored signals."
        )
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("pending"))
    async def pending(message: Message) -> None:
        async with database.sessions() as session:
            repository = SignalRepository(session)
            await SignalService(repository, settings).expire_due(datetime.now(UTC))
            rows = await repository.list_signals(
                statuses=["WAITING_ENTRY", "REVALIDATING"], limit=100
            )
        text = (
            "🟡 <b>PENDING SETUPS — PAPER ONLY</b>\n\n"
            + "\n\n".join(format_pending(row, index) for index, row in enumerate(rows, 1))
            if rows
            else "<b>PENDING SETUPS</b>\n\nNo setup is currently waiting for entry."
        )
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("signals"))
    async def signals(message: Message) -> None:
        async with database.sessions() as session:
            rows = await SignalRepository(session).list_signals(limit=10)
        text = (
            "\n\n".join(format_signal(row) for row in rows) if rows else "No persisted signals yet."
        )
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("directional_stats"))
    async def directional_stats(message: Message) -> None:
        if not _diagnostics_allowed(message, settings):
            await message.answer("This read-only diagnostic is restricted to administrators.")
            return
        async with database.sessions() as session:
            data = await directional_diagnostics(session, hours=24)
        await message.answer(format_directional_stats(data))

    @router.message(Command("near_misses"))
    async def near_misses(message: Message) -> None:
        if not _diagnostics_allowed(message, settings):
            await message.answer("This read-only diagnostic is restricted to administrators.")
            return
        async with database.sessions() as session:
            data = await directional_diagnostics(session, hours=24)
        await _answer_chunks(
            message,
            format_near_misses(data["near_misses"]),
            settings.telegram_max_message_length,
        )

    @router.message(Command("signal"))
    async def signal_detail(message: Message, command: CommandObject) -> None:
        if not command.args:
            await message.answer("Usage: /signal SIGNAL_ID")
            return
        async with database.sessions() as session:
            row = await SignalRepository(session).get_signal(command.args.strip())
        await message.answer(format_signal(row) if row else "Signal not found.")

    async def send_portfolio(message: Message, positions_only: bool = False) -> None:
        identity = _identity(message)
        async with database.sessions() as session:
            repo = PortfolioRepository(session)
            user = await repo.get_user_by_telegram_id(identity.id)
            account = await repo.active_account(user.id) if user else None
            if account is None:
                await message.answer("Complete /start to create a virtual paper balance.")
                return
            positions = await repo.list_positions(account.id)
        text = (
            "\n\n".join(format_position(x) for x in positions)
            if positions_only
            else format_portfolio(account, positions)
        )
        await _answer_chunks(
            message, text or "No paper positions.", settings.telegram_max_message_length
        )

    for command_name in ("portfolio", "balance", "pnl"):
        router.message.register(send_portfolio, Command(command_name))

    @router.message(Command("connections"))
    async def connections(message: Message) -> None:
        accounts = await account_service.get_accounts() if account_service else ()
        await message.answer(
            format_connections(
                accounts, scanner=scanner_state.safe_dict() if scanner_state else None
            )
        )

    @router.message(Command("scanner_status"))
    async def scanner_status(message: Message) -> None:
        if scanner_state is None:
            await message.answer("<b>SCANNER STATUS</b>\n\nRuntime not started.")
            return
        state = scanner_state.safe_dict()
        await message.answer(
            "<b>SCANNER STATUS</b>\n\n"
            f"Universe: {state['universe_status']}\n"
            f"Directional: {state['directional_status']} "
            f"({state['directional_symbols']} symbols)\n"
            f"Arbitrage: {state['arbitrage_status']} "
            f"({state['arbitrage_symbols']} symbols)\n"
            f"Last refresh: {state['last_universe_refresh'] or 'never'}\n"
            f"Last scan: {state['last_directional_scan'] or 'never'}\n"
            f"Actionable: {state['latest_actionable']} | "
            f"Pending: {state['latest_pending']} | "
            f"Watchlist: {state['latest_watchlist']} | NO TRADE: {state['latest_no_trade']}\n"
            f"Arbitrage candidates: {state['latest_arbitrage_candidates']} | "
            f"Qualified: {state['latest_arbitrage_qualified']}\n\n"
            f"WebSocket events: {state['websocket_events_processed']} | "
            f"Reconnects: {state['websocket_reconnects']}\n\n"
            "Trading execution: 🔒 DISABLED"
        )

    @router.message(Command("system_status"))
    async def system_status_command(message: Message) -> None:
        await message.answer(
            system_status.telegram_text()
            if system_status is not None
            else "<b>SYSTEM STATUS</b>\n\nHealth supervisor not started."
        )

    @router.message(Command("accounts", "real_balance"))
    async def real_accounts(message: Message) -> None:
        accounts = await account_service.get_accounts() if account_service else ()
        text = (
            "\n\n".join(format_account(row) for row in accounts)
            or "<b>REAL ACCOUNTS — READ ONLY</b>\n\nPrivate account connections are disabled."
        )
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("account_binance", "account_bybit"))
    async def real_account(message: Message) -> None:
        exchange = "BINANCE" if (message.text or "").startswith("/account_binance") else "BYBIT"
        row = await account_service.get_account(exchange) if account_service else None
        await message.answer(
            format_account(row)
            if row
            else f"<b>{exchange} — REAL ACCOUNT — READ ONLY</b>\n\nConnection disabled."
        )

    @router.message(Command("real_positions"))
    async def real_positions(message: Message) -> None:
        accounts = await account_service.get_accounts() if account_service else ()
        await _answer_chunks(
            message, format_positions(accounts), settings.telegram_max_message_length
        )

    @router.message(Command("report", "report_week", "report_month", "stats"))
    async def reports(message: Message) -> None:
        command = (message.text or "/report").split()[0]
        kind = {
            "/report": "DAILY",
            "/report_week": "WEEKLY",
            "/report_month": "MONTHLY",
            "/stats": "ALL_TIME",
        }[command]
        identity = _identity(message)
        async with database.sessions() as session:
            repo = PortfolioRepository(session)
            user = await repo.get_user_by_telegram_id(identity.id)
            account = await repo.active_account(user.id) if user else None
            if account is None:
                await message.answer("Complete /start first.")
                return
            report = await ReportService(repo).generate(account, kind)
        await message.answer(format_report(report))

    @router.message(Command("settings"))
    async def show_settings(message: Message) -> None:
        identity = _identity(message)
        async with database.sessions() as session:
            user = await PortfolioRepository(session).get_user_by_telegram_id(identity.id)
        if user is None:
            await message.answer("Complete /start first.")
            return
        p = user.preferences
        await message.answer(
            "⚙️ <b>PAPER SETTINGS</b>\n"
            f"Exchanges: {', '.join(p.enabled_exchanges)}\n"
            f"Markets: {', '.join(p.enabled_markets)}\n"
            f"Minimum confidence: {p.minimum_confidence}\n"
            f"Paper Simulation Profile: {p.paper_profile}\n"
            f"Signal notifications: {p.receive_signal_notifications}\n"
            f"Monitoring updates: {p.receive_monitoring_updates}\n"
            f"Report timezone: {user.timezone}"
        )

    @router.message(Command("reset_paper"))
    async def reset(message: Message, state: FSMContext) -> None:
        await state.set_state(ResetPaper.confirm)
        await message.answer(
            "This archives virtual performance only. No real account is affected.",
            reply_markup=reset_keyboard(),
        )

    @router.callback_query(ResetPaper.confirm, F.data.startswith("reset:"))
    async def reset_confirm(callback: CallbackQuery, state: FSMContext) -> None:
        if callback.data == "reset:CANCEL":
            await state.clear()
            await callback.answer("Cancelled")
            return
        await state.set_state(ResetPaper.balance)
        await callback.answer()
        if callback.message:
            await callback.message.answer("Enter the new starting virtual USDT balance.")

    @router.message(ResetPaper.balance)
    async def reset_balance(message: Message, state: FSMContext) -> None:
        identity = _identity(message)
        try:
            async with database.sessions() as session:
                repo = PortfolioRepository(session)
                user = await repo.get_user_by_telegram_id(identity.id)
                if user is None:
                    raise ValueError("Complete /start first")
                account = await UserService(repo).reset_account(user, message.text or "")
        except ValueError as exc:
            await message.answer(str(exc))
            return
        await state.clear()
        await message.answer(f"Virtual paper account reset to {account.starting_balance} USDT.")

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "Commands: /start /top /pending /active /signals /signal ID /portfolio /balance "
            "/pnl /report /report_week /report_month /stats /settings /reset_paper "
            "/arbitrage /arbitrage_top /arbitrage_active /arbitrage_history "
            "/arbitrage_stats /directional_stats /near_misses /help\n\n"
            "/connections /scanner_status /system_status /accounts /account_binance "
            "/account_bybit /real_balance "
            "/real_positions\n\nSignal, paper trading, and real account read-only viewing. "
            "No real order execution."
        )

    @router.message(Command("arbitrage", "arbitrage_top"))
    async def arbitrage_top(message: Message) -> None:
        async with database.sessions() as session:
            rows = await ArbitrageRepository(session).list(statuses=("QUALIFIED",), limit=100)
            rows.sort(key=lambda row: (-row.confidence_score, -float(row.net_edge_pct), row.id))
        await _answer_chunks(
            message, format_arbitrage_top(rows[:5]), settings.telegram_max_message_length
        )

    @router.message(Command("arbitrage_active", "arbitrage_history"))
    async def arbitrage_positions(message: Message) -> None:
        identity = _identity(message)
        async with database.sessions() as session:
            repo = PortfolioRepository(session)
            user = await repo.get_user_by_telegram_id(identity.id)
            account = await repo.active_account(user.id) if user else None
            if account is None:
                await message.answer("Complete /start first.")
                return
            status = "OPEN" if (message.text or "").startswith("/arbitrage_active") else None
            rows = await repo.list_arbitrage_positions(account.id, status)
        text = (
            "\n\n".join(format_paper_arbitrage(row) for row in rows)
            if rows
            else "No paper arbitrage positions."
        )
        await _answer_chunks(message, text, settings.telegram_max_message_length)

    @router.message(Command("arbitrage_stats"))
    async def arbitrage_stats(message: Message) -> None:
        async with database.sessions() as session:
            rows = await ArbitrageRepository(session).list(limit=1000)
        qualified = [row for row in rows if row.status in {"QUALIFIED", "PAPER_ACTIVE", "CLOSED"}]
        avg = (
            sum((Decimal(str(row.net_edge_pct)) for row in qualified), Decimal("0"))
            / Decimal(len(qualified))
            if qualified
            else Decimal("0")
        )
        await message.answer(
            "📈 <b>ARBITRAGE STATISTICS</b>\n\n"
            f"Persisted opportunities: {len(rows)}\nQualified: {len(qualified)}\n"
            f"Average qualified net edge: {avg:.4f}%\n\nPaper simulation only."
        )

    return router


async def _answer_chunks(message: Message, text: str, limit: int) -> None:
    for start in range(0, len(text), limit):
        await message.answer(text[start : start + limit])


def _identity(message: Message) -> User:
    if message.from_user is None:
        raise ValueError("Telegram user identity is unavailable")
    return message.from_user


def _diagnostics_allowed(message: Message, settings: Settings) -> bool:
    identity = _identity(message)
    return not settings.telegram_admin_ids or identity.id in settings.telegram_admin_ids
