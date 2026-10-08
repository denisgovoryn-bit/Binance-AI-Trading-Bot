"""Stable identities for an underlying arbitrage route."""

import hashlib

from app.arbitrage.models import ArbitrageType
from app.market.models import ExchangeName, MarketType


def opportunity_fingerprint(
    arbitrage_type: ArbitrageType,
    symbol: str,
    buy_exchange: ExchangeName,
    buy_market: MarketType,
    sell_exchange: ExchangeName,
    sell_market: MarketType,
    *,
    funding_interval: str = "",
) -> str:
    canonical = "|".join(
        (
            arbitrage_type,
            symbol.upper(),
            buy_exchange,
            buy_market,
            sell_exchange,
            sell_market,
            funding_interval,
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
