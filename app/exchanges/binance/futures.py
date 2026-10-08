"""Binance USDT-M Futures adapter."""

from app.exchanges.binance.adapter import BinanceExchange
from app.market.models import MarketType


class BinanceFuturesExchange(BinanceExchange):
    def __init__(self) -> None:
        super().__init__(MarketType.FUTURES)
