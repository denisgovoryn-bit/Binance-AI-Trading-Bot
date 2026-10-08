"""Sanitized retrying transport for authenticated read-only GET requests."""

import asyncio
import os
import ssl
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp


class PrivateAPIError(RuntimeError):
    """A safe error which never contains request URLs, headers, or credentials."""

    def __init__(self, message: str, *, code: str | int | None = None) -> None:
        super().__init__(message)
        self.code = str(code) if code is not None else None

    @property
    def public_message(self) -> str:
        prefix = f"code {self.code}: " if self.code is not None else ""
        return f"{prefix}{self.args[0]}"


class PrivateRESTClient:
    def __init__(
        self, *, session: aiohttp.ClientSession | None = None, max_retries: int = 3
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self.max_retries = max_retries

    async def get(self, url: str, *, params: Mapping[str, Any], headers: Mapping[str, str]) -> Any:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                connector=aiohttp.TCPConnector(ssl=verified_ssl_context()),
            )
        for attempt in range(self.max_retries):
            try:
                async with self._session.get(url, params=params, headers=headers) as response:
                    payload = await response.json(content_type=None)
                    if response.status in {418, 429} or response.status >= 500:
                        if attempt + 1 < self.max_retries:
                            await asyncio.sleep(min(2**attempt, 5))
                            continue
                    if response.status >= 400:
                        raise PrivateAPIError(
                            f"authenticated API request failed (HTTP {response.status})"
                        )
                    if not isinstance(payload, (dict, list)):
                        raise PrivateAPIError("authenticated API returned an invalid response")
                    return payload
            except PrivateAPIError:
                raise
            except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
                if attempt + 1 == self.max_retries:
                    error_type = type(exc).__name__
                    raise PrivateAPIError(
                        f"authenticated API connection failed ({error_type})"
                    ) from exc
                await asyncio.sleep(min(2**attempt, 5))
        raise PrivateAPIError("authenticated API retries exhausted")

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()


def verified_ssl_context() -> ssl.SSLContext:
    """Build a verified context from explicit or standard platform CA bundles."""
    configured = os.getenv("SSL_CERT_FILE")
    if configured:
        return ssl.create_default_context(cafile=configured)
    platform_bundle = Path("/etc/ssl/cert.pem")
    if platform_bundle.is_file():
        return ssl.create_default_context(cafile=str(platform_bundle))
    return ssl.create_default_context()
