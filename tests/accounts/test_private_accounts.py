from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.accounts.models import AccountConnectionStatus, AccountPosition, ExchangeAccount
from app.accounts.service import AccountService
from app.exchanges.binance.account import BinanceAccountAdapter
from app.exchanges.bybit.account import BYBIT_ENDPOINTS, BybitAccountAdapter
from app.exchanges.private_http import PrivateAPIError


class FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def get(self, url: str, *, params: object, headers: object) -> object:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        pass


def test_binance_hmac_signing_is_deterministic() -> None:
    adapter = BinanceAccountAdapter("key", "secret", FakeClient([]))  # type: ignore[arg-type]
    signed = adapter.signed_params({"symbol": "BTCUSDT"}, now_ms=1_700_000_000_000)
    assert signed["signature"] == "898122a528961a351a3a8321656401f94a9273b6b8a3eee071845e9c573a755e"


def test_bybit_hmac_signing_is_deterministic_and_regions_are_explicit() -> None:
    adapter = BybitAccountAdapter("key", "secret", "EEA", FakeClient([]))  # type: ignore[arg-type]
    headers = adapter.signed_headers({"category": "linear"}, now_ms=1_700_000_000_000)
    assert (
        headers["X-BAPI-SIGN"] == "e6c3e971c517d999338172674f1c633b9016addf8f8c632372232076767b4c07"
    )
    assert adapter.base_url == BYBIT_ENDPOINTS["EEA"]


@pytest.mark.asyncio
async def test_binance_normalizes_spot_balances_and_empty_values() -> None:
    payload = {
        "canTrade": False,
        "balances": [
            {"asset": "USDT", "free": "2.5", "locked": "1"},
            {"asset": "BTC", "free": "0", "locked": "0"},
        ],
    }
    adapter = BinanceAccountAdapter("key", "secret", FakeClient([payload]))  # type: ignore[arg-type]
    balances = await adapter.get_balances()
    assert len(balances) == 1
    assert balances[0].available == Decimal("2.5")


@pytest.mark.asyncio
async def test_bybit_normalizes_wallet_and_positions() -> None:
    wallet = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "coin": [
                        {
                            "coin": "USDT",
                            "walletBalance": "5",
                            "equity": "6",
                            "availableToWithdraw": "4",
                            "locked": "1",
                        }
                    ]
                }
            ]
        },
    }
    positions = {
        "retCode": 0,
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.1",
                    "avgPrice": "10",
                    "markPrice": "11",
                    "unrealisedPnl": "0.1",
                    "leverage": "2",
                    "liqPrice": "5",
                }
            ]
        },
    }
    adapter = BybitAccountAdapter("key", "secret", client=FakeClient([wallet, positions]))  # type: ignore[arg-type]
    assert (await adapter.get_wallet_balance())[0].equity == Decimal("6")
    assert (await adapter.get_open_positions())[0].direction == "BUY"


class Reader:
    def __init__(self, account: ExchangeAccount | Exception) -> None:
        self.account = account
        self.calls = 0

    async def snapshot(self) -> ExchangeAccount:
        self.calls += 1
        if isinstance(self.account, Exception):
            raise self.account
        return self.account

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_account_cache_and_authentication_failure_are_safe() -> None:
    reader = Reader(
        ExchangeAccount(
            exchange="BINANCE",
            status=AccountConnectionStatus.READ_ONLY_PERMISSION_CONFIRMED,
            permission_confirmed=True,
        )
    )
    service = AccountService({"BINANCE": reader}, refresh_seconds=30)
    assert await service.get_account("BINANCE") == await service.get_account("BINANCE")
    assert reader.calls == 1
    failed = AccountService({"BYBIT": Reader(PrivateAPIError("authentication failed", code=10003))})
    account = await failed.get_account("BYBIT")
    assert account.status is AccountConnectionStatus.AUTHENTICATION_FAILED
    assert account.error == "code 10003: authentication failed"


@pytest.mark.asyncio
async def test_real_positions_are_never_paper_models() -> None:
    position = AccountPosition(exchange="BYBIT", symbol="ETHUSDT", direction="LONG", quantity="1")
    account = ExchangeAccount(
        exchange="BYBIT", positions=(position,), refreshed_at=datetime.now(UTC)
    )
    assert account.mode == "REAL ACCOUNT — READ ONLY"
    assert "paper" not in account.model_dump_json().lower()
