"""Sanitized live validation using harmless authenticated read-only requests only."""

import asyncio

from aiogram import Bot

from app.accounts.factory import create_account_service
from app.config.settings import Settings


async def main() -> None:
    settings = Settings()
    if settings.telegram_enabled and settings.telegram_bot_token:
        bot = Bot(settings.telegram_bot_token)
        try:
            identity = await bot.get_me()
            print(f"Telegram: OK @{identity.username}")
        except Exception:
            print("Telegram: FAILED")
        finally:
            await bot.session.close()
    else:
        print("Telegram: DISABLED")

    print(f"Bybit region: {settings.bybit_api_region}")
    service = create_account_service(settings)
    try:
        accounts = await service.get_accounts(force=True)
        found = {account.exchange for account in accounts}
        for account in accounts:
            permission = "READ_ONLY" if account.permission_confirmed else account.status.value
            outcome = "OK" if not account.error else "FAILED"
            print(f"{account.exchange.title()} private: {outcome} {permission}")
            if account.error:
                print(f"  error: {account.error}")
            print(
                f"  non-zero balances: {len(account.balances)}; "
                f"open positions: {len(account.positions)}"
            )
        if "BINANCE" not in found:
            reason = (
                "DISABLED" if not settings.binance_private_api_enabled else "FAILED CONFIGURATION"
            )
            print(f"Binance private: {reason}")
        if "BYBIT" not in found:
            reason = (
                "FAILED REGION_UNRESOLVED"
                if settings.bybit_private_api_enabled and settings.bybit_api_region == "AUTO"
                else "DISABLED"
            )
            print(f"Bybit private: {reason}")
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
