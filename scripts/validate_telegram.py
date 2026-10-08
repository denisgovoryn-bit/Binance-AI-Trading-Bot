"""Validate Telegram identity, handler setup, and database without sending messages."""

import asyncio

from aiogram import Bot

from app.config.settings import Settings
from app.db.session import Database
from app.telegram.bot import create_dispatcher


async def main() -> None:
    settings = Settings()
    if not settings.telegram_bot_token:
        print("Telegram: FAILED (token not configured)")
        return
    database = Database(settings)
    bot = Bot(settings.telegram_bot_token)
    try:
        await database.create_schema()
        identity = await bot.get_me()
        dispatcher = create_dispatcher(database, settings)
        database_ok = await database.healthy()
        print(f"Telegram: OK @{identity.username}")
        print(f"Handlers: OK ({len(dispatcher.sub_routers)} router initialized)")
        print(f"Database: {'OK' if database_ok else 'FAILED'}")
        print("No message sent. Send /start to persist a Telegram user/chat record.")
    except Exception:
        print("Telegram: FAILED")
    finally:
        await bot.session.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
