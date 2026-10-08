"""FastAPI application factory."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.accounts.factory import create_account_service
from app.api.routes.accounts import router as accounts_router
from app.api.routes.arbitrage import router as arbitrage_router
from app.api.routes.health import router as health_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.signals import router as signals_router
from app.config.settings import Settings, get_settings
from app.db.session import Database
from app.logging import configure_logging
from app.runtime.scanners import ScannerRuntimeState


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application with explicit, testable dependencies."""

    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)
    database = Database(resolved_settings)
    account_service = create_account_service(resolved_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.database = database
        await database.create_schema()
        await account_service.get_accounts(force=True)
        yield
        await account_service.close()
        await database.close()

    application = FastAPI(
        title="Crypto Signal Bot",
        version="0.1.0",
        description="Signal research and paper trading only; no real-money execution.",
        lifespan=lifespan,
    )
    application.state.database = database
    application.state.settings = resolved_settings
    application.state.account_service = account_service
    application.state.telegram_status = (
        "NOT_CONNECTED" if resolved_settings.telegram_enabled else "DISABLED"
    )
    application.state.scanner_state = ScannerRuntimeState()
    application.include_router(accounts_router)
    application.include_router(health_router)
    application.include_router(arbitrage_router)
    application.include_router(signals_router)
    application.include_router(portfolio_router)
    return application


app = create_app()
