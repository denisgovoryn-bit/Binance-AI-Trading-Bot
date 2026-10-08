"""Deterministic executable order-book VWAP."""

from decimal import Decimal

from app.arbitrage.models import BookLevel, VWAPResult


def buy_vwap(
    levels: tuple[BookLevel, ...], requested_notional: Decimal, allow_partial: bool = False
) -> VWAPResult:
    if requested_notional <= 0:
        raise ValueError("requested notional must be positive")
    remaining = requested_notional
    cost = Decimal("0")
    quantity = Decimal("0")
    consumed = 0
    for level in levels:
        available = level.price * level.quantity
        take = min(remaining, available)
        if take <= 0:
            continue
        cost += take
        quantity += take / level.price
        remaining -= take
        consumed += 1
        if remaining <= 0:
            break
    return _result(
        requested_notional, cost, quantity, consumed, levels[0].price, remaining <= 0, allow_partial
    )


def sell_vwap(
    levels: tuple[BookLevel, ...],
    requested_quantity: Decimal,
    reference_notional: Decimal,
    allow_partial: bool = False,
) -> VWAPResult:
    if requested_quantity <= 0 or reference_notional <= 0:
        raise ValueError("quantity and notional must be positive")
    remaining = requested_quantity
    proceeds = Decimal("0")
    quantity = Decimal("0")
    consumed = 0
    for level in levels:
        take = min(remaining, level.quantity)
        if take <= 0:
            continue
        proceeds += take * level.price
        quantity += take
        remaining -= take
        consumed += 1
        if remaining <= 0:
            break
    return _result(
        reference_notional,
        proceeds,
        quantity,
        consumed,
        levels[0].price,
        remaining <= 0,
        allow_partial,
    )


def _result(
    requested: Decimal,
    executable: Decimal,
    quantity: Decimal,
    consumed: int,
    best: Decimal,
    complete: bool,
    allow_partial: bool,
) -> VWAPResult:
    if not complete and not allow_partial:
        raise ValueError("insufficient order-book depth")
    if quantity <= 0:
        raise ValueError("no executable depth")
    vwap = executable / quantity
    slip = abs(vwap - best) / best * Decimal("100")
    return VWAPResult(
        vwap=vwap,
        requested_notional=requested,
        executable_notional=executable,
        quantity=quantity,
        levels_consumed=consumed,
        best_price=best,
        slippage_pct=slip,
        liquidity_ratio=min(Decimal("1"), executable / requested),
        complete=complete,
    )
