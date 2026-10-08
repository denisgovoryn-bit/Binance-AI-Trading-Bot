"""Risk/reward math without probability assumptions."""

from app.signals.models import (
    EntryZone,
    InvalidationLevel,
    PaperExitAllocation,
    RiskRewardMetrics,
    SignalDirection,
    SignalTarget,
)


def r_multiple(
    target_price: float,
    entry: EntryZone,
    invalidation: InvalidationLevel,
    direction: SignalDirection,
) -> float | None:
    risk = abs(entry.ideal_entry - invalidation.price)
    reward = (
        target_price - entry.ideal_entry if direction.bullish else entry.ideal_entry - target_price
    )
    return reward / risk if risk > 0 and reward > 0 else None


def build_risk_reward(
    entry: EntryZone,
    invalidation: InvalidationLevel,
    targets: tuple[SignalTarget, ...],
    allocations: tuple[float, ...],
) -> tuple[RiskRewardMetrics, tuple[PaperExitAllocation, ...]]:
    risk = abs(entry.ideal_entry - invalidation.price)
    raw_allocations = allocations[: len(targets)]
    total = sum(raw_allocations)
    normalized = tuple(value / total for value in raw_allocations)
    weighted = sum(
        target.r_multiple * weight for target, weight in zip(targets, normalized, strict=True)
    )
    plan = tuple(
        PaperExitAllocation(target_name=target.name, percentage=weight * 100)
        for target, weight in zip(targets, normalized, strict=True)
    )
    return (
        RiskRewardMetrics(
            reference_entry=entry.ideal_entry,
            risk_distance=risk,
            target_r_multiples={target.name: target.r_multiple for target in targets},
            best_r=max(target.r_multiple for target in targets),
            weighted_r=weighted,
        ),
        plan,
    )
