"""Transparent virtual exposure sizing; never real-money advice."""

from dataclasses import dataclass
from decimal import Decimal

from app.config.settings import Settings
from app.portfolio.models import PaperProfile


@dataclass(frozen=True)
class PaperSize:
    notional: Decimal
    quantity: Decimal
    leverage: Decimal
    method: str


def size_position(
    equity: Decimal, entry: Decimal, profile: PaperProfile, market: str, settings: Settings
) -> PaperSize:
    percentages = {
        PaperProfile.CONSERVATIVE: Decimal(str(settings.paper_conservative_exposure_pct)),
        PaperProfile.BALANCED: Decimal(str(settings.paper_balanced_exposure_pct)),
        PaperProfile.AGGRESSIVE: Decimal(str(settings.paper_aggressive_exposure_pct)),
    }
    leverage = Decimal(str(settings.paper_futures_leverage if market == "FUTURES" else 1))
    notional = (equity * percentages[profile] / Decimal("100") * leverage).quantize(
        Decimal("0.00000001")
    )
    quantity = (notional / entry).quantize(Decimal("0.00000001"))
    if quantity <= 0:
        raise ValueError("paper sizing produced non-positive quantity")
    method = f"{profile.value} paper profile: {percentages[profile]}% equity at {leverage}x"
    return PaperSize(notional, quantity, leverage, method)
