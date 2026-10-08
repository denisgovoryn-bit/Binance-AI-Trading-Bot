"""Replaceable public-assumption fee provider."""

from decimal import Decimal

from app.config.settings import Settings


class ArbitrageFeeProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def taker_fee_pct(self, exchange: str, market: str) -> Decimal:
        values = {
            ("BINANCE", "SPOT"): self.settings.fee_binance_spot_bps,
            ("BINANCE", "FUTURES"): self.settings.fee_binance_futures_bps,
            ("BYBIT", "SPOT"): self.settings.fee_bybit_spot_bps,
            ("BYBIT", "FUTURES"): self.settings.fee_bybit_futures_bps,
        }
        return Decimal(str(values[(exchange, market)])) / Decimal("100")
