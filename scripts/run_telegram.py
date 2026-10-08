"""Run the primary Telegram paper-trading UI."""

import asyncio

from app.config.settings import Settings
from app.db.session import Database
from app.logging import configure_logging
from app.runtime.lock import AlreadyRunningError, ProcessLock
from app.telegram.bot import run_bot


async def main() -> None:
    settings = Settings()
    configure_logging(
        settings.log_level,
        log_file=settings.runtime_log_file,
        max_bytes=settings.runtime_log_max_bytes,
        backup_count=settings.runtime_log_backup_count,
    )
    database = Database(settings)
    await database.create_schema()
    try:
        await run_bot(database, settings)
    finally:
        await database.close()


if __name__ == "__main__":
    settings = Settings()
    try:
        with ProcessLock(settings.runtime_pid_file):
            asyncio.run(main())
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from None
