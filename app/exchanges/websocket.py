"""Reusable reconnecting WebSocket JSON stream."""

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

import websockets

from app.exchanges.private_http import verified_ssl_context

logger = logging.getLogger(__name__)


class WebSocketConnection(Protocol):
    def __aiter__(self) -> AsyncIterator[str | bytes]: ...

    async def send(self, message: str) -> None: ...

    async def close(self) -> None: ...


Connector = Callable[[str], Awaitable[WebSocketConnection]]


async def default_connector(url: str) -> WebSocketConnection:
    return await websockets.connect(
        url,
        # Both venues drive their own heartbeat. websockets still answers incoming
        # protocol ping frames automatically when client-initiated pings are disabled.
        ping_interval=None,
        close_timeout=2,
        ssl=verified_ssl_context(),
    )


class ReconnectingJSONStream:
    """Yield decoded messages, reconnecting with capped exponential backoff."""

    def __init__(
        self,
        url: str,
        *,
        subscribe_message: dict[str, Any] | None = None,
        connector: Connector = default_connector,
        base_delay: float = 0.5,
        maximum_delay: float = 30,
        exchange: str = "UNKNOWN",
        market: str = "UNKNOWN",
        topics: tuple[str, ...] = (),
        heartbeat_message: dict[str, Any] | None = None,
        heartbeat_interval: float = 20,
    ) -> None:
        self.url = url
        self.subscribe_message = subscribe_message
        self.connector = connector
        self.base_delay = base_delay
        self.maximum_delay = maximum_delay
        self.exchange = exchange
        self.market = market
        self.topics = topics
        self.heartbeat_message = heartbeat_message
        self.heartbeat_interval = heartbeat_interval
        self._closed = False
        self._connection: WebSocketConnection | None = None
        self._valid_event_since_connect = False
        self._first_event_logged = False
        self.reconnect_count = 0
        self.subscription_acknowledged = subscribe_message is None
        self.last_connection_id: str | None = None
        self.last_pong_at: float | None = None
        self.last_backoff_seconds = 0.0

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or "unknown"

    def mark_market_event(self) -> None:
        """Confirm a decoded payload was normalized into usable market data."""
        self._valid_event_since_connect = True
        if not self._first_event_logged:
            self._first_event_logged = True
            logger.info(
                "websocket_first_market_event",
                extra=self._diagnostics(),
            )

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        attempt = 0
        while not self._closed:
            heartbeat: asyncio.Task[None] | None = None
            try:
                connection = await self.connector(self.url)
                self._connection = connection
                self._valid_event_since_connect = False
                self._first_event_logged = False
                self.subscription_acknowledged = self.subscribe_message is None
                logger.info("websocket_connected", extra=self._diagnostics())
                if self.subscribe_message is not None:
                    await connection.send(json.dumps(self.subscribe_message))
                    logger.info("websocket_subscription_sent", extra=self._diagnostics())
                if self.heartbeat_message is not None:
                    heartbeat = asyncio.create_task(self._heartbeat(connection))
                async for raw_message in connection:
                    if self._closed:
                        return
                    decoded = json.loads(raw_message)
                    if isinstance(decoded, dict):
                        self._inspect_control_message(decoded)
                        yield decoded
                if not self._closed:
                    raise ConnectionError("websocket stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closed:
                    return
                if self._valid_event_since_connect:
                    attempt = 0
                delay = min(self.base_delay * (2**attempt), self.maximum_delay)
                delay *= random.uniform(0.8, 1.2)
                self.last_backoff_seconds = delay
                diagnostics = self._diagnostics()
                diagnostics.update(
                    {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "backoff_seconds": round(delay, 3),
                    }
                )
                logger.warning("websocket_reconnecting", extra=diagnostics)
                self.reconnect_count += 1
                attempt += 1
                await asyncio.sleep(delay)
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(self, connection: WebSocketConnection) -> None:
        while not self._closed:
            await asyncio.sleep(self.heartbeat_interval)
            if self.heartbeat_message is not None:
                await connection.send(json.dumps(self.heartbeat_message))
                logger.debug("websocket_application_ping", extra=self._diagnostics())

    def _inspect_control_message(self, message: dict[str, Any]) -> None:
        connection_id = message.get("conn_id")
        if isinstance(connection_id, str):
            self.last_connection_id = connection_id
        if message.get("op") in {"ping", "pong"} or message.get("ret_msg") == "pong":
            self.last_pong_at = asyncio.get_running_loop().time()
            logger.debug("websocket_application_pong", extra=self._diagnostics())
        if message.get("op") == "subscribe" or (
            "result" in message and message.get("id") is not None
        ):
            success = message.get("success", message.get("result") is None)
            if success:
                if not self.subscription_acknowledged:
                    self.subscription_acknowledged = True
                    logger.info("websocket_subscription_acknowledged", extra=self._diagnostics())
                return
            code = message.get("retCode", message.get("code", "unknown"))
            reason = message.get("ret_msg", message.get("msg", "subscription rejected"))
            logger.error(
                "websocket_subscription_rejected",
                extra={**self._diagnostics(), "error_code": code, "error": str(reason)},
            )
            raise ConnectionError(f"subscription rejected ({code}): {reason}")

    def _diagnostics(self) -> dict[str, object]:
        return {
            "exchange": self.exchange,
            "market": self.market,
            "host": self.host,
            "topics": list(self.topics),
            "topic_count": len(self.topics),
            "connection_id": self.last_connection_id,
        }

    async def close(self) -> None:
        self._closed = True
        if self._connection is not None:
            await self._connection.close()
