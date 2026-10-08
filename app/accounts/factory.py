"""Construct account readers only when explicitly enabled."""

from app.accounts.service import AccountReader, AccountService
from app.config.settings import Settings
from app.exchanges.binance.account import BinanceAccountAdapter
from app.exchanges.bybit.account import BybitAccountAdapter


def create_account_service(settings: Settings) -> AccountService:
    readers: dict[str, AccountReader] = {}
    if settings.binance_private_api_enabled:
        if settings.binance_api_key and settings.binance_api_secret:
            readers["BINANCE"] = BinanceAccountAdapter(
                settings.binance_api_key, settings.binance_api_secret
            )
    if settings.bybit_private_api_enabled:
        if (
            settings.bybit_api_key
            and settings.bybit_api_secret
            and settings.bybit_api_region != "AUTO"
        ):
            readers["BYBIT"] = BybitAccountAdapter(
                settings.bybit_api_key, settings.bybit_api_secret, settings.bybit_api_region
            )
    return AccountService(readers, settings.private_account_refresh_seconds)
