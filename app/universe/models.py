"""Normalized symbol universe models."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.market.models import ExchangeName, MarketType


class UniverseEntry(BaseModel):
    symbol: str
    exchange: ExchangeName
    market: MarketType
    base_asset: str
    quote_asset: str
    active: bool
    quote_volume_24h: Decimal
    spread_pct: Decimal
    listed_at: datetime | None = None
    listing_age_days: int | None = None
    cross_venue_count: int = 1
    refreshed_at: datetime


class UniverseSnapshot(BaseModel):
    entries: tuple[UniverseEntry, ...]
    refreshed_at: datetime
    degraded_markets: tuple[str, ...] = ()
    used_fallback: bool = False

    @property
    def directional(self) -> tuple[UniverseEntry, ...]:
        selected: dict[tuple[MarketType, str], UniverseEntry] = {}
        for row in self.entries:
            key = (row.market, row.symbol)
            current = selected.get(key)
            if current is None or row.quote_volume_24h > current.quote_volume_24h:
                selected[key] = row
        return tuple(sorted(selected.values(), key=lambda row: (row.market, row.symbol)))

    def symbols(self, exchange: ExchangeName, market: MarketType) -> tuple[str, ...]:
        return tuple(
            row.symbol for row in self.entries if row.exchange is exchange and row.market is market
        )

    def common(self, market: MarketType) -> tuple[str, ...]:
        binance = set(self.symbols(ExchangeName.BINANCE, market))
        bybit = set(self.symbols(ExchangeName.BYBIT, market))
        return tuple(sorted(binance & bybit))

    @property
    def cross_exchange_spot(self) -> tuple[str, ...]:
        return self.common(MarketType.SPOT)

    @property
    def cross_exchange_futures(self) -> tuple[str, ...]:
        return self.common(MarketType.FUTURES)

    @property
    def funding(self) -> tuple[str, ...]:
        return self.cross_exchange_futures

    @property
    def basis(self) -> tuple[tuple[ExchangeName, str], ...]:
        result: list[tuple[ExchangeName, str]] = []
        for exchange in ExchangeName:
            spot = set(self.symbols(exchange, MarketType.SPOT))
            futures = set(self.symbols(exchange, MarketType.FUTURES))
            result.extend((exchange, symbol) for symbol in sorted(spot & futures))
        return tuple(result)
