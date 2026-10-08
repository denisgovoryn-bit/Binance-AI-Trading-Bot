"""Bybit V5 authenticated account reader. No mutation methods exist here."""

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

BYBIT_ENDPOINTS = {
    "STANDARD": "https://api.bybit.com",
    "EEA": "https://api.bybit.eu",
    "TESTNET": "https://api-testnet.bybit.com",
    "DEMO": "https://api-demo.bybit.com",
}


class BybitAccountAdapter:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        region: str = "STANDARD",
        client: PrivateRESTClient | None = None,
    ) -> None:
        if not api_key or not api_secret:
            raise ValueError("Bybit private credentials are required")
        if region not in BYBIT_ENDPOINTS:
            raise ValueError("unsupported Bybit API region")
        self._api_key, self._secret, self.region = api_key, api_secret.encode(), region
        self.base_url = BYBIT_ENDPOINTS[region]
        self.client = client or PrivateRESTClient()

    def signed_headers(
        self, params: dict[str, Any] | None = None, *, now_ms: int | None = None
    ) -> dict[str, str]:
        timestamp, recv_window = str(now_ms or int(time.time() * 1000)), "5000"
        query = urlencode(sorted((params or {}).items()))
        raw = f"{timestamp}{self._api_key}{recv_window}{query}"
        signature = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
        }

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = params or {}
        payload = await self.client.get(
            f"{self.base_url}{path}", params=query, headers=self.signed_headers(query)
        )
        if payload.get("retCode") != 0:
            code = payload.get("retCode", "unknown")
            message = self._sanitize_message(str(payload.get("retMsg") or "request failed"))
            raise PrivateAPIError(message, code=code)
        result = payload.get("result", {})
        return result if isinstance(result, dict) else {}

    def _sanitize_message(self, message: str) -> str:
        """Remove credentials if an upstream error unexpectedly reflects them."""
        safe = message.replace(self._api_key, "[REDACTED]")
        secret = self._secret.decode(errors="ignore")
        if secret:
            safe = safe.replace(secret, "[REDACTED]")
        return safe[:200]

    async def get_api_key_information(self) -> dict[str, Any]:
        return await self._get("/v5/user/query-api")

    async def get_wallet_balance(self, account_type: str = "UNIFIED") -> tuple[AccountBalance, ...]:
        result = await self._get("/v5/account/wallet-balance", {"accountType": account_type})
        balances: list[AccountBalance] = []
        for account in result.get("list", []):
            for coin in account.get("coin", []):
                wallet = Decimal(coin.get("walletBalance", "0") or "0")
                equity = Decimal(coin.get("equity", "0") or "0")
                if wallet or equity:
                    balances.append(
                        AccountBalance(
                            exchange="BYBIT",
                            asset=coin["coin"],
                            available=coin.get("availableToWithdraw", "0") or "0",
                            locked=coin.get("locked", "0") or "0",
                            wallet=wallet,
                            equity=equity,
                            account_type=account_type,
                        )
                    )
        return tuple(balances)

    async def get_open_positions(
        self, category: str = "linear", settle_coin: str = "USDT"
    ) -> tuple[AccountPosition, ...]:
        result = await self._get(
            "/v5/position/list", {"category": category, "settleCoin": settle_coin}
        )
        return tuple(
            AccountPosition(
                exchange="BYBIT",
                symbol=x["symbol"],
                direction=x.get("side", "").upper(),
                quantity=x.get("size", "0"),
                entry_price=x.get("avgPrice") or None,
                mark_price=x.get("markPrice") or None,
                unrealized_pnl=x.get("unrealisedPnl") or None,
                leverage=x.get("leverage") or None,
                liquidation_price=x.get("liqPrice") or None,
            )
            for x in result.get("list", [])
            if Decimal(x.get("size", "0")) != 0
        )

    async def get_open_orders(self, category: str = "linear") -> tuple[AccountOrder, ...]:
        result = await self._get("/v5/order/realtime", {"category": category, "openOnly": 0})
        return tuple(
            AccountOrder(
                exchange="BYBIT",
                order_id=x["orderId"],
                symbol=x["symbol"],
                side=x["side"],
                order_type=x["orderType"],
                status=x["orderStatus"],
                price=x.get("price") or None,
                quantity=x.get("qty") or None,
            )
            for x in result.get("list", [])
        )

    async def get_recent_executions(
        self, category: str = "linear", limit: int = 50
    ) -> tuple[AccountExecution, ...]:
        result = await self._get("/v5/execution/list", {"category": category, "limit": limit})
        return tuple(
            AccountExecution(
                exchange="BYBIT",
                execution_id=x["execId"],
                symbol=x["symbol"],
                side=x["side"],
                price=x["execPrice"],
                quantity=x["execQty"],
                timestamp=datetime.fromtimestamp(int(x["execTime"]) / 1000, UTC),
            )
            for x in result.get("list", [])
        )

    async def snapshot(self) -> ExchangeAccount:
        key_info = await self.get_api_key_information()
        read_only = int(key_info.get("readOnly", 0)) == 1
        return ExchangeAccount(
            exchange="BYBIT",
            status=AccountConnectionStatus.READ_ONLY_PERMISSION_CONFIRMED
            if read_only
            else AccountConnectionStatus.TRADING_PERMISSION_DETECTED,
            permission_confirmed=read_only,
            trading_permission_detected=not read_only,
            balances=await self.get_wallet_balance(),
            positions=await self.get_open_positions(),
        )

    async def close(self) -> None:
        await self.client.close()
