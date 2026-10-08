"""Bybit Spot adapter."""

from app.exchanges.bybit.adapter import BybitExchange
from app.market.models import MarketType


class BybitSpotExchange(BybitExchange):
    def __init__(self) -> None:
        super().__init__(MarketType.SPOT)
