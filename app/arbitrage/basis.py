"""Same-venue spot/perpetual basis candidates."""

from dataclasses import dataclass
from decimal import Decimal

from app.arbitrage.engine import calculate_cross_book
from app.arbitrage.fees import ArbitrageFeeProvider
from app.arbitrage.models import ArbitrageEconomics, BookSnapshot
from app.config.settings import Settings
from app.market.models import MarketType


@dataclass(frozen=True)
class BasisCandidate:
    symbol: str
    economics: ArbitrageEconomics
    basis_pct: Decimal
    reverse: bool
    warnings: tuple[str, ...] = ()


def evaluate_basis(
    spot: BookSnapshot,
    futures: BookSnapshot,
    notional: Decimal,
    fees: ArbitrageFeeProvider,
    settings: Settings,
    *,
    allow_reverse_detection: bool = True,
) -> BasisCandidate:
    if spot.exchange != futures.exchange or spot.symbol != futures.symbol:
        raise ValueError("basis instruments must share exchange and symbol")
    if spot.market is not MarketType.SPOT or futures.market is not MarketType.FUTURES:
        raise ValueError("spot and USDT futures books are required")
    midpoint_spot = (spot.bids[0].price + spot.asks[0].price) / Decimal("2")
    midpoint_futures = (futures.bids[0].price + futures.asks[0].price) / Decimal("2")
    basis = (midpoint_futures - midpoint_spot) / midpoint_spot * Decimal("100")
    if basis >= 0:
        economics = calculate_cross_book(spot, futures, notional, fees, settings)
        return BasisCandidate(spot.symbol, economics, basis, False)
    if not allow_reverse_detection:
        raise ValueError("reverse basis requires spot borrowing")
    economics = calculate_cross_book(futures, spot, notional, fees, settings)
    return BasisCandidate(
        spot.symbol,
        economics,
        basis,
        True,
        ("Reverse basis is detection-only because Spot short borrowing is not supported",),
    )
