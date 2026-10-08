"""Configurable paper fee and slippage assumptions."""

from decimal import Decimal

from app.config.settings import Settings


def fee_bps(exchange: str, market: str, settings: Settings) -> Decimal:
    key = (exchange.upper(), market.upper())
    values = {
        ("BINANCE", "SPOT"): settings.fee_binance_spot_bps,
        ("BINANCE", "FUTURES"): settings.fee_binance_futures_bps,
        ("BYBIT", "SPOT"): settings.fee_bybit_spot_bps,
        ("BYBIT", "FUTURES"): settings.fee_bybit_futures_bps,
    }
    return Decimal(str(values[key]))


def apply_slippage(price: Decimal, bullish: bool, entry: bool, settings: Settings) -> Decimal:
    bps = Decimal(
        str(settings.paper_entry_slippage_bps if entry else settings.paper_stop_slippage_bps)
    )
    adverse = Decimal("1") + (bps / Decimal("10000")) * (Decimal("1") if bullish else Decimal("-1"))
    return (price * adverse).quantize(Decimal("0.00000001"))


def apply_stop_slippage(price: Decimal, bullish: bool, settings: Settings) -> Decimal:
    bps = Decimal(str(settings.paper_stop_slippage_bps)) / Decimal("10000")
    factor = Decimal("1") - bps if bullish else Decimal("1") + bps
    return (price * factor).quantize(Decimal("0.00000001"))


def calculate_fee(price: Decimal, quantity: Decimal, bps: Decimal) -> Decimal:
    return (price * quantity * bps / Decimal("10000")).quantize(Decimal("0.00000001"))
