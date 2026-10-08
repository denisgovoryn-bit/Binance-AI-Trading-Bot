"""Exchange-independent real-account view models (never paper accounts)."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field


class AccountConnectionStatus(StrEnum):
    DISABLED = "DISABLED"
    NOT_CONNECTED = "NOT_CONNECTED"
    CONNECTED_READ_ONLY = "CONNECTED_READ_ONLY"
    READ_ONLY_PERMISSION_CONFIRMED = "READ_ONLY_PERMISSION_CONFIRMED"
    TRADING_PERMISSION_DETECTED = "TRADING_PERMISSION_DETECTED"
    INSUFFICIENT_PERMISSION = "INSUFFICIENT_PERMISSION"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"


class AccountBalance(BaseModel):
    exchange: str
    asset: str
    available: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    wallet: Decimal | None = None
    equity: Decimal | None = None
    account_type: str = "SPOT"


class AccountPosition(BaseModel):
    exchange: str
    symbol: str
    direction: str
    quantity: Decimal
    entry_price: Decimal | None = None
    mark_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    leverage: Decimal | None = None
    liquidation_price: Decimal | None = None


class AccountOrder(BaseModel):
    exchange: str
    order_id: str = Field(repr=False)
    symbol: str
    side: str
    order_type: str
    status: str
    price: Decimal | None = None
    quantity: Decimal | None = None


class AccountExecution(BaseModel):
    exchange: str
    execution_id: str = Field(repr=False)
    symbol: str
    side: str
    price: Decimal
    quantity: Decimal
    timestamp: datetime


class ExchangeAccount(BaseModel):
    exchange: str
    mode: str = "REAL ACCOUNT — READ ONLY"
    status: AccountConnectionStatus = AccountConnectionStatus.NOT_CONNECTED
    permission_confirmed: bool = False
    trading_permission_detected: bool = False
    balances: tuple[AccountBalance, ...] = ()
    positions: tuple[AccountPosition, ...] = ()
    orders: tuple[AccountOrder, ...] = ()
    executions: tuple[AccountExecution, ...] = ()
    refreshed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
