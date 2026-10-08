"""Binance authenticated account reader. This module exposes GET operations only."""

import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from app.accounts.models import (
    AccountBalance,
    AccountConnectionStatus,
    AccountExecution,
    AccountOrder,
    AccountPosition,
    ExchangeAccount,
)
from app.exchanges.private_http import PrivateAPIError, PrivateRESTClient


class BinanceAccountAdapter:
    spot_url = "https://api.binance.com"
    futures_url = "https://fapi.binance.com"

    def __init__(
        self, api_key: str, api_secret: str, client: PrivateRESTClient | None = None
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Binance private credentials are required")
        self._api_key = api_key
        self._secret = api_secret.encode()
        self.client = client or PrivateRESTClient()

    def signed_params(
        self, params: dict[str, Any] | None = None, *, now_ms: int | None = None
    ) -> dict[str, Any]:
        result = dict(params or {})
        result.update(timestamp=now_ms or int(time.time() * 1000), recvWindow=5000)
        query = urlencode(result)
        result["signature"] = hmac.new(self._secret, query.encode(), hashlib.sha256).hexdigest()
        return result

    async def _get(self, base: str, path: str, params: dict[str, Any] | None = None) -> Any:
        payload = await self.client.get(
            f"{base}{path}",
            params=self.signed_params(params),
            headers={"X-MBX-APIKEY": self._api_key},
        )
        if isinstance(payload, dict) and "code" in payload and int(payload["code"]) < 0:
            raise PrivateAPIError(
                f"Binance authentication/account request failed ({payload['code']})"
            )
        return payload

    async def get_account_information(self) -> dict[str, Any]:
        payload = await self._get(self.spot_url, "/api/v3/account", {"omitZeroBalances": "true"})
        return payload if isinstance(payload, dict) else {}

    async def get_balances(self) -> tuple[AccountBalance, ...]:
        payload = await self.get_account_information()
        return tuple(
            AccountBalance(
                exchange="BINANCE",
                asset=row["asset"],
                available=row.get("free", "0"),
                locked=row.get("locked", "0"),
                account_type="SPOT",
            )
            for row in payload.get("balances", [])
            if Decimal(row.get("free", "0")) != 0 or Decimal(row.get("locked", "0")) != 0
        )

    async def get_futures_account_summary(self) -> dict[str, Any]:
        payload = await self._get(self.futures_url, "/fapi/v3/account")
        return payload if isinstance(payload, dict) else {}

    async def get_open_futures_positions(self) -> tuple[AccountPosition, ...]:
        payload = await self._get(self.futures_url, "/fapi/v3/positionRisk")
        rows = payload if isinstance(payload, list) else payload.get("positions", [])
        return tuple(
            AccountPosition(
                exchange="BINANCE",
                symbol=row["symbol"],
                direction="LONG" if Decimal(row.get("positionAmt", "0")) > 0 else "SHORT",
                quantity=abs(Decimal(row.get("positionAmt", "0"))),
                entry_price=row.get("entryPrice"),
                mark_price=row.get("markPrice"),
                unrealized_pnl=row.get("unRealizedProfit"),
                leverage=row.get("leverage"),
                liquidation_price=row.get("liquidationPrice") or None,
            )
            for row in rows
            if Decimal(row.get("positionAmt", "0")) != 0
        )

    async def get_open_orders(self, symbol: str | None = None) -> tuple[AccountOrder, ...]:
        params = {"symbol": symbol} if symbol else {}
        payload = await self._get(self.spot_url, "/api/v3/openOrders", params)
        rows = payload if isinstance(payload, list) else []
        return tuple(
            AccountOrder(
                exchange="BINANCE",
                order_id=str(x["orderId"]),
                symbol=x["symbol"],
                side=x["side"],
                order_type=x["type"],
                status=x["status"],
                price=x.get("price"),
                quantity=x.get("origQty"),
            )
            for x in rows
        )

    async def get_recent_trade_history(
        self, symbol: str, limit: int = 50
    ) -> tuple[AccountExecution, ...]:
        payload = await self._get(
            self.spot_url, "/api/v3/myTrades", {"symbol": symbol, "limit": limit}
        )
        rows = payload if isinstance(payload, list) else []
        return tuple(
            AccountExecution(
                exchange="BINANCE",
                execution_id=str(x["id"]),
                symbol=x["symbol"],
                side="BUY" if x.get("isBuyer") else "SELL",
                price=x["price"],
                quantity=x["qty"],
                timestamp=datetime.fromtimestamp(x["time"] / 1000, UTC),
            )
            for x in rows
        )

    async def snapshot(self) -> ExchangeAccount:
        account = await self.get_account_information()
        trading = bool(account.get("canTrade"))
        balances = await self.get_balances()
        positions: tuple[AccountPosition, ...] = ()
        try:
            positions = await self.get_open_futures_positions()
        except PrivateAPIError:
            pass
        return ExchangeAccount(
            exchange="BINANCE",
            status=AccountConnectionStatus.TRADING_PERMISSION_DETECTED
            if trading
            else AccountConnectionStatus.READ_ONLY_PERMISSION_CONFIRMED,
            permission_confirmed=not trading,
            trading_permission_detected=trading,
            balances=balances,
            positions=positions,
        )

    async def close(self) -> None:
        await self.client.close()
