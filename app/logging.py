"""Structured JSON logging configuration."""

import logging
import logging.config
import os
from pathlib import Path


class SecretRedactionFilter(logging.Filter):
    """Redact configured credentials from messages and exception text."""

    def __init__(self) -> None:
        super().__init__()
        names = (
            "TELEGRAM_BOT_TOKEN",
            "BINANCE_API_KEY",
            "BINANCE_API_SECRET",
            "BYBIT_API_KEY",
            "BYBIT_API_SECRET",
        )
        self._secrets = tuple(value for name in names if (value := os.getenv(name)))

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg, record.args = message, ()
        for key, value in tuple(record.__dict__.items()):
            if isinstance(value, str):
                for secret in self._secrets:
                    value = value.replace(secret, "[REDACTED]")
                record.__dict__[key] = value
            elif isinstance(value, list):
                record.__dict__[key] = [
                    self._redact_string(item) if isinstance(item, str) else item for item in value
                ]
        if record.exc_info:
            record.exc_text = "[REDACTED EXCEPTION: see internal cause]"
            record.exc_info = None
        return True

    def _redact_string(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value


def configure_logging(
    level: str = "INFO",
    *,
    log_file: str | None = None,
    max_bytes: int = 10_485_760,
    backup_count: int = 10,
) -> None:
    """Configure JSON logs suitable for local and container execution."""

    handlers: dict[str, dict[str, object]] = {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["redact"],
        }
    }
    root_handlers = ["default"]
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "json",
            "filters": ["redact"],
            "filename": str(path),
            "maxBytes": max_bytes,
            "backupCount": backup_count,
            "encoding": "utf-8",
        }
        root_handlers.append("file")
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {
                    "()": "pythonjsonlogger.json.JsonFormatter",
                    "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
                }
            },
            "handlers": handlers,
            "filters": {"redact": {"()": "app.logging.SecretRedactionFilter"}},
            "root": {"handlers": root_handlers, "level": level.upper()},
        }
    )
