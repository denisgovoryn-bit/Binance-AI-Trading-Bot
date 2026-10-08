"""Rate-limit-aware public REST transport."""

import asyncio
from typing import Any

import aiohttp

from app.exchanges.private_http import verified_ssl_context


class ExchangeAPIError(RuntimeError):
    """Public exchange API returned an unusable response."""


class PublicRESTClient:
    def __init__(
        self,
        base_url: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout_seconds: float = 10,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = session
        self._owns_session = session is None
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self.max_retries = max_retries

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=aiohttp.TCPConnector(ssl=verified_ssl_context()),
            )
        for attempt in range(self.max_retries):
            try:
                async with self._session.get(f"{self.base_url}{path}", params=params) as response:
                    if response.status in {418, 429} or response.status >= 500:
                        if attempt + 1 < self.max_retries:
                            await asyncio.sleep(min(2**attempt, 5))
                            continue
                    if response.status >= 400:
                        body = await response.text()
                        raise ExchangeAPIError(f"HTTP {response.status}: {body[:200]}")
                    return await response.json()
            except (aiohttp.ClientError, TimeoutError) as exc:
                if attempt + 1 == self.max_retries:
                    raise ExchangeAPIError(str(exc)) from exc
                await asyncio.sleep(min(2**attempt, 5))
        raise ExchangeAPIError("request retries exhausted")

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
