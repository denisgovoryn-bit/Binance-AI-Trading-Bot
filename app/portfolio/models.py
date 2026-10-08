"""Paper portfolio domain values and transparent sizing rules."""

from decimal import Decimal, InvalidOperation
from enum import StrEnum


class PaperProfile(StrEnum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"


class PaperTradeMode(StrEnum):
    AUTO_PAPER = "AUTO_PAPER"
    MANUAL_PAPER = "MANUAL_PAPER"


class PaperPositionStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    CLOSED = "CLOSED"


class PaperFillType(StrEnum):
    ENTRY = "ENTRY"
    TP1 = "TP1"
    TP2 = "TP2"
    TP3 = "TP3"
    STOP = "STOP"
    CLOSE = "CLOSE"


def parse_balance(
    value: str, minimum: Decimal = Decimal("0.01"), maximum: Decimal = Decimal("1000000000")
) -> Decimal:
    try:
        amount = Decimal(value.strip().upper().removesuffix("USDT").strip())
    except InvalidOperation as exc:
        raise ValueError("Enter a valid virtual USDT amount, for example 1000") from exc
    if not amount.is_finite() or amount < minimum or amount > maximum:
        raise ValueError(f"Virtual balance must be between {minimum} and {maximum} USDT")
    return amount.quantize(Decimal("0.01"))
