"""Cached aggregation of real, read-only exchange account state."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.accounts.models import AccountConnectionStatus, ExchangeAccount
from app.exchanges.private_http import PrivateAPIError

logger = logging.getLogger(__name__)


class AccountReader(Protocol):
    async def snapshot(self) -> ExchangeAccount: ...
    async def close(self) -> None: ...


class AccountService:
    def __init__(self, readers: dict[str, AccountReader], refresh_seconds: float = 30) -> None:
        self._readers = readers
        self._refresh = timedelta(seconds=refresh_seconds)
        self._cache: dict[str, ExchangeAccount] = {}
        self._locks = {name: asyncio.Lock() for name in readers}

    def configured_exchanges(self) -> tuple[str, ...]:
        return tuple(self._readers)

    async def get_account(self, exchange: str, *, force: bool = False) -> ExchangeAccount:
        name = exchange.upper()
        reader = self._readers.get(name)
        if reader is None:
            return ExchangeAccount(exchange=name, status=AccountConnectionStatus.DISABLED)
        cached = self._cache.get(name)
        if not force and cached and datetime.now(UTC) - cached.refreshed_at < self._refresh:
            return cached
        async with self._locks[name]:
            cached = self._cache.get(name)
            if not force and cached and datetime.now(UTC) - cached.refreshed_at < self._refresh:
                return cached
            try:
                account = await reader.snapshot()
                if account.trading_permission_detected:
                    logger.warning(
                        "SECURITY WARNING: %s API key has trading permission; "
                        "execution remains disabled",
                        name,
                    )
            except PrivateAPIError as exc:
                logger.error("%s private account authentication/refresh failed", name)
                account = ExchangeAccount(
                    exchange=name,
                    status=AccountConnectionStatus.AUTHENTICATION_FAILED,
                    error=exc.public_message,
                )
            except Exception:
                logger.error("%s private account authentication/refresh failed", name)
                account = ExchangeAccount(
                    exchange=name,
                    status=AccountConnectionStatus.AUTHENTICATION_FAILED,
                    error="Private account unavailable",
                )
            self._cache[name] = account
            return account

    async def get_accounts(self, *, force: bool = False) -> tuple[ExchangeAccount, ...]:
        return tuple(
            await asyncio.gather(*(self.get_account(name, force=force) for name in self._readers))
        )

    def health(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for name in ("BINANCE", "BYBIT"):
            cached = self._cache.get(name)
            result[name.lower()] = (
                cached.status.value
                if cached
                else ("NOT_CONNECTED" if name in self._readers else "DISABLED")
            )
        return result

    async def close(self) -> None:
        await asyncio.gather(*(reader.close() for reader in self._readers.values()))
