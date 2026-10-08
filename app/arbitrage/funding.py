"""Market-neutral next-interval funding comparison."""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, Decimal

from app.config.settings import Settings
from app.market.models import ExchangeName, FundingRate


@dataclass(frozen=True)
class FundingCandidate:
    symbol: str
    long_exchange: ExchangeName
    short_exchange: ExchangeName
    long_rate_pct: Decimal
    short_rate_pct: Decimal
    next_interval_advantage_pct: Decimal
    round_trip_cost_pct: Decimal
    break_even_intervals: int | None
    next_funding_at: datetime
    timestamp_skew_minutes: Decimal
    warnings: tuple[str, ...]


def evaluate_funding(
    first: FundingRate,
    second: FundingRate,
    round_trip_cost_pct: Decimal,
    settings: Settings,
    *,
    basis_risk_pct: Decimal = Decimal("0"),
) -> FundingCandidate:
    if first.symbol != second.symbol or first.exchange == second.exchange:
        raise ValueError("equivalent contracts on different venues are required")
    # Positive funding is paid by longs to shorts; choose the lower rate for the long.
    long, short = sorted((first, second), key=lambda item: item.rate)
    advantage = (short.rate - long.rate) * Decimal("100")
    intervals = None
    if advantage > 0:
        intervals = int((round_trip_cost_pct / advantage).to_integral_value(rounding=ROUND_CEILING))
    skew = abs(
        Decimal(str((first.funding_time - second.funding_time).total_seconds())) / Decimal("60")
    )
    warnings: list[str] = []
    if skew > Decimal(settings.arbitrage_max_funding_time_skew_minutes):
        warnings.append("Funding timestamps differ beyond the configured tolerance")
    if basis_risk_pct * Decimal("100") > Decimal(str(settings.arbitrage_max_basis_risk_bps)):
        warnings.append("Cross-venue price basis risk exceeds the configured tolerance")
    if advantage <= 0 or intervals is None:
        warnings.append("Funding differential does not offset trading costs")
    elif intervals > 1:
        warnings.append(f"Estimated costs require {intervals} funding intervals to recover")
    return FundingCandidate(
        symbol=first.symbol,
        long_exchange=long.exchange,
        short_exchange=short.exchange,
        long_rate_pct=long.rate * Decimal("100"),
        short_rate_pct=short.rate * Decimal("100"),
        next_interval_advantage_pct=advantage,
        round_trip_cost_pct=round_trip_cost_pct,
        break_even_intervals=intervals,
        next_funding_at=min(first.funding_time, second.funding_time),
        timestamp_skew_minutes=skew,
        warnings=tuple(warnings),
    )
