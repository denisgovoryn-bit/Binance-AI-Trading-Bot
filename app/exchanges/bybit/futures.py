"""Bybit Linear Futures adapter."""

from app.exchanges.bybit.adapter import BybitExchange
from app.market.models import MarketType


class BybitFuturesExchange(BybitExchange):
    def __init__(self) -> None:
        super().__init__(MarketType.FUTURES)
