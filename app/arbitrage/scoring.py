"""Transparent arbitrage opportunity-quality score (not win probability)."""

from decimal import Decimal

from app.arbitrage.models import ArbitrageClassification, ScoreComponent
from app.config.settings import Settings

D = Decimal


def score_opportunity(
    *,
    net_edge_pct: Decimal,
    liquidity_ratio: Decimal,
    persistence_ms: Decimal,
    quote_age_ms: Decimal,
    timestamp_skew_ms: Decimal,
    spread_instability: Decimal,
    slippage_pct: Decimal,
    settings: Settings,
) -> tuple[Decimal, tuple[ScoreComponent, ...], ArbitrageClassification]:
    edge_target = max(D("0.01"), D(str(settings.arbitrage_min_net_edge_bps)) / D("100"))
    values = (
        ("net_edge", min(D("30"), max(D("0"), net_edge_pct / edge_target * D("20"))), D("30")),
        ("liquidity", min(D("20"), liquidity_ratio * D("20")), D("20")),
        (
            "persistence",
            min(
                D("15"),
                persistence_ms / max(D("1"), D(settings.arbitrage_min_persistence_ms)) * D("15"),
            ),
            D("15"),
        ),
        (
            "freshness",
            max(D("0"), D("10") * (D("1") - quote_age_ms / D(settings.arbitrage_max_quote_age_ms))),
            D("10"),
        ),
        (
            "alignment",
            max(
                D("0"),
                D("10")
                * (
                    D("1")
                    - timestamp_skew_ms
                    / max(D("1"), D(settings.arbitrage_max_cross_exchange_skew_ms))
                ),
            ),
            D("10"),
        ),
        ("stability", max(D("0"), D("10") * (D("1") - min(D("1"), spread_instability))), D("10")),
        (
            "execution",
            max(
                D("0"),
                D("5")
                * (
                    D("1")
                    - slippage_pct
                    * D("100")
                    / max(D("1"), D(str(settings.arbitrage_max_slippage_bps)))
                ),
            ),
            D("5"),
        ),
    )
    components = tuple(
        ScoreComponent(
            name=name,
            score=score,
            max_score=maximum,
            reasons=(f"{name} measured deterministically",),
        )
        for name, score, maximum in values
    )
    total = min(D("100"), sum((item.score for item in components), D("0")))
    if total >= D(str(settings.arbitrage_very_strong_score)):
        classification = ArbitrageClassification.VERY_STRONG_OPPORTUNITY
    elif total >= D(str(settings.arbitrage_strong_score)):
        classification = ArbitrageClassification.STRONG_OPPORTUNITY
    elif total >= D(str(settings.arbitrage_watchlist_score)):
        classification = ArbitrageClassification.WATCHLIST
    else:
        classification = ArbitrageClassification.REJECTED
    return total, components, classification
