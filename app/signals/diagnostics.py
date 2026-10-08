"""Read-only directional decision-funnel analytics."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from statistics import fmean, median
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.directional_models import DirectionalEvaluationRecord, DirectionalScanCycleRecord
from app.db.portfolio_models import UserNotificationDeliveryRecord
from app.db.signal_models import (
    SignalLifecycleEventRecord,
    SignalMonitoringEventRecord,
    SignalRecord,
)

BUCKETS = (
    (0, 49),
    (50, 59),
    (60, 69),
    (70, 74),
    (75, 79),
    (80, 84),
    (85, 89),
    (90, 94),
    (95, 100),
)


async def directional_diagnostics(session: AsyncSession, *, hours: int = 24) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    evaluations = list(
        (
            await session.execute(
                select(DirectionalEvaluationRecord).where(
                    DirectionalEvaluationRecord.evaluated_at >= cutoff
                )
            )
        ).scalars()
    )
    cycles = list(
        (
            await session.execute(
                select(DirectionalScanCycleRecord).where(
                    DirectionalScanCycleRecord.started_at >= cutoff
                )
            )
        ).scalars()
    )
    signals = list(
        (
            await session.execute(select(SignalRecord).where(SignalRecord.created_at >= cutoff))
        ).scalars()
    )
    lifecycle = list(
        (
            await session.execute(
                select(SignalLifecycleEventRecord).where(SignalLifecycleEventRecord.at >= cutoff)
            )
        ).scalars()
    )
    monitoring = list(
        (
            await session.execute(
                select(SignalMonitoringEventRecord).where(
                    SignalMonitoringEventRecord.received_at >= cutoff
                )
            )
        ).scalars()
    )
    deliveries = list(
        (
            await session.execute(
                select(UserNotificationDeliveryRecord).where(
                    UserNotificationDeliveryRecord.delivery_key.like("signal:%")
                )
            )
        ).scalars()
    )
    selected = [row for row in evaluations if row.selected]
    scorable = [row for row in evaluations if row.scorable]
    scores = sorted(row.confidence_score for row in scorable)
    rejection_rows = [row for row in evaluations if row.classification == "NO_TRADE"]
    primary = Counter(
        categorize_reason(row.primary_rejection_reason or "other") for row in rejection_rows
    )
    all_reasons = Counter(
        categorize_reason(reason) for row in rejection_rows for reason in row.rejection_reasons
    )
    single = Counter(
        categorize_reason(row.rejection_reasons[0])
        for row in rejection_rows
        if len(row.rejection_reasons) == 1
    )
    near_misses = sorted(
        (
            row
            for row in rejection_rows
            if row.scorable and row.confidence_score >= 65 and len(row.rejection_reasons) <= 2
        ),
        key=lambda row: row.confidence_score,
        reverse=True,
    )[:20]
    monitoring_counts = Counter(row.event_type for row in monitoring)
    lifecycle_counts = Counter(row.new_status for row in lifecycle)
    delivery_counts = Counter(row.status for row in deliveries if _in_window(row.sent_at, cutoff))
    delivered_rows = [row for row in deliveries if _in_window(row.sent_at, cutoff)]
    market_funnel: dict[str, dict[str, int]] = {}
    for row in selected:
        key = f"{row.exchange} {row.market}"
        counts = market_funnel.setdefault(key, {})
        counts["evaluations"] = counts.get("evaluations", 0) + 1
        counts[row.classification] = counts.get(row.classification, 0) + 1
        counts[row.final_state] = counts.get(row.final_state, 0) + 1
        zone_state = row.payload.get("supporting_metrics", {}).get("entry_zone_state")
        if row.final_state == "WAITING_ENTRY" and isinstance(zone_state, str):
            counts[zone_state] = counts.get(zone_state, 0) + 1
    score_distribution = {
        f"{low}-{high}": sum(low <= score <= high for score in scores) for low, high in BUCKETS
    }
    positions = [
        row
        for row in signals
        if row.entry_confirmed_at is not None and row.waiting_since is not None
    ]
    waiting_seconds = [
        (_aware(row.entry_confirmed_at) - _aware(row.waiting_since)).total_seconds()
        for row in positions
        if row.entry_confirmed_at is not None and row.waiting_since is not None
    ]
    creation_distances = [
        value
        for row in signals
        if isinstance(
            value := row.payload.get("supporting_metrics", {}).get("distance_to_entry_zone_atr"),
            (int, float),
        )
    ]
    return {
        "window": {"hours": hours, "start": cutoff, "end": now},
        "audit_available": bool(evaluations),
        "coverage": {
            "cycles": len(cycles),
            "completed": sum(row.status == "COMPLETED" for row in cycles),
            "partial": sum(row.status == "PARTIAL" for row in cycles),
            "failed_or_interrupted": sum(row.status == "RUNNING" for row in cycles),
            "expected_markets": sum(row.expected_markets for row in cycles),
            "evaluated_markets": len(selected),
            "market_data_failures": sum(row.failed_markets for row in cycles),
            "missing_or_interrupted_markets": max(
                sum(row.expected_markets for row in cycles) - len(selected), 0
            ),
            "unique_symbols": len({row.symbol for row in selected}),
        },
        "candidate_evaluations": {
            "total": len(evaluations),
            "LONG": sum(row.direction == "LONG" for row in evaluations),
            "SHORT": sum(row.direction == "SHORT" for row in evaluations),
            "BUY": sum(row.direction == "BUY" for row in evaluations),
            "unscorable_hard_rejections": sum(not row.scorable for row in evaluations),
        },
        "market_funnel": market_funnel,
        "states": Counter(row.final_state for row in selected),
        "funnel": {
            "NO_TRADE": sum(row.classification == "NO_TRADE" for row in selected),
            "WATCHLIST": sum(row.classification == "WATCHLIST" for row in selected),
            "WAITING_ENTRY": sum(row.final_state == "WAITING_ENTRY" for row in selected),
            "ACTIONABLE": sum(
                row.final_state == "WAITING_ENTRY"
                and row.payload.get("supporting_metrics", {}).get("entry_zone_state")
                == "ACTIONABLE_NOW"
                for row in selected
            ),
            "PENDING_ENTRY": sum(
                row.final_state == "WAITING_ENTRY"
                and row.payload.get("supporting_metrics", {}).get("entry_zone_state")
                == "PENDING_ENTRY"
                for row in selected
            ),
            "ENTRY_CONFIRMED": lifecycle_counts["ENTRY_CONFIRMED"],
            "ACTIVE": lifecycle_counts["ACTIVE"],
            "INVALIDATED": lifecycle_counts["INVALIDATED"],
            "EXPIRED": lifecycle_counts["EXPIRED"],
            "REJECTED": sum(row.final_state == "REJECTED" for row in selected),
        },
        "persistence_actions": Counter(row.persistence_action for row in selected),
        "scores": {
            "population": len(scores),
            "buckets": score_distribution,
            "mean": fmean(scores) if scores else None,
            "median": median(scores) if scores else None,
            "p75": percentile(scores, 75),
            "p90": percentile(scores, 90),
            "p95": percentile(scores, 95),
            "maximum": max(scores) if scores else None,
            "gte_80": sum(score >= 80 for score in scores),
            "gte_85": sum(score >= 85 for score in scores),
            "gte_90": sum(score >= 90 for score in scores),
        },
        "top_evaluations": [serialize_evaluation(row) for row in _top(evaluations, 20)],
        "rejections": {
            "primary": dict(primary.most_common()),
            "all": dict(all_reasons.most_common()),
            "single_blockers": dict(single.most_common()),
        },
        "near_misses": [serialize_evaluation(row) for row in near_misses[:10]],
        "pending": {
            "created": lifecycle_counts["WAITING_ENTRY"],
            "approached": monitoring_counts["ENTRY_APPROACHING"],
            "zones_reached": monitoring_counts["REVALIDATION_STARTED"],
            "revalidations_attempted": monitoring_counts["REVALIDATION_STARTED"],
            "passes": monitoring_counts["ENTRY_CONFIRMED"],
            "failures": monitoring_counts["REVALIDATION_FAILED"],
            "expired": lifecycle_counts["EXPIRED"],
            "structurally_invalidated": lifecycle_counts["INVALIDATED"],
            "average_waiting_seconds": fmean(waiting_seconds) if waiting_seconds else None,
            "average_creation_distance_atr": (
                fmean(creation_distances) if creation_distances else None
            ),
            "average_creation_score": (
                fmean(row.initial_confidence for row in signals) if signals else None
            ),
            "average_revalidation_score": (
                fmean(
                    row.confidence_at_entry
                    for row in positions
                    if row.confidence_at_entry is not None
                )
                if any(row.confidence_at_entry is not None for row in positions)
                else None
            ),
            "average_confidence_delta": (
                fmean(
                    row.confidence_delta_at_entry
                    for row in positions
                    if row.confidence_delta_at_entry is not None
                )
                if any(row.confidence_delta_at_entry is not None for row in positions)
                else None
            ),
            "failure_reasons": dict(
                Counter(
                    categorize_reason(reason)
                    for row in monitoring
                    if row.event_type == "REVALIDATION_FAILED"
                    for reason in row.reasons
                ).most_common()
            ),
        },
        "notifications": {
            "signals_persisted": len(signals),
            "lifecycle_events": len(lifecycle),
            "monitoring_events": dict(monitoring_counts),
            "deliveries": dict(delivery_counts),
            "delivery_records_all_time": len(deliveries),
            "pending_all_time": sum(row.status == "PENDING" for row in deliveries),
            "telegram_confirmed_sent": sum(
                row.status == "SENT" and row.telegram_message_id is not None
                for row in delivered_rows
            ),
            "validation_transport_without_message_id": sum(
                row.status == "SENT" and row.telegram_message_id is None for row in delivered_rows
            ),
            "failures": sum(row.status == "FAILED" for row in deliveries),
            "failure_window_note": (
                "Failed delivery rows have no creation timestamp; failure count is all-time."
            ),
            "deduplicated": sum(
                row.persistence_action == "DUPLICATE_SUPPRESSED" for row in selected
            ),
        },
        "market_structure": _market_structure(selected),
    }


def serialize_evaluation(row: DirectionalEvaluationRecord) -> dict[str, Any]:
    payload = row.payload
    components = {item["name"]: item["score"] for item in row.score_components}
    return {
        "timestamp": row.evaluated_at,
        "exchange": row.exchange,
        "market": row.market,
        "symbol": row.symbol,
        "direction": row.direction,
        "score": row.confidence_score,
        "components": components,
        "final_state": row.final_state,
        "primary_rejection_reason": row.primary_rejection_reason,
        "hard_filter_reasons": row.rejection_reasons,
        "current_price": payload.get("supporting_metrics", {}).get("current_price"),
        "entry_zone": payload.get("entry_zone"),
        "stop": payload.get("invalidation"),
        "targets": payload.get("targets", []),
        "projected_risk_reward": (payload.get("risk_reward") or {}).get("best_r"),
        "diagnostic_label": "NOT A SIGNAL — NOT AN ENTRY RECOMMENDATION",
    }


def categorize_reason(reason: str) -> str:
    text = reason.lower()
    rules = (
        ("poor_risk_reward", ("risk/reward", "structural target offers")),
        ("volatility_rejection", ("volatility",)),
        ("failed_breakout", ("failed breakout", "breakout failed")),
        ("degraded_history", ("history", "data quality", "degraded")),
        ("htf_conflict", ("higher timeframe", "alignment")),
        ("invalid_structure", ("structure", "swing")),
        ("momentum_conflict", ("momentum", "rsi", "macd")),
        ("insufficient_volume", ("volume",)),
        ("overextended", ("overextend", "extension")),
        ("invalid_stop", ("stop", "invalidation")),
        ("invalid_targets", ("target",)),
        ("stale_data", ("stale", "fresh")),
        ("btc_context_conflict", ("btc",)),
        ("insufficient_history", ("insufficient", "only ")),
        ("spread_execution_quality", ("spread", "execution")),
        ("no_valid_entry", ("entry",)),
        ("score_below_threshold", ("score ", "threshold")),
    )
    return next((label for label, terms in rules if any(term in text for term in terms)), "other")


def percentile(values: list[float], percentage: int) -> float | None:
    if not values:
        return None
    index = (len(values) - 1) * percentage / 100
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    fraction = index - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def _top(
    evaluations: list[DirectionalEvaluationRecord], limit: int
) -> list[DirectionalEvaluationRecord]:
    return sorted(evaluations, key=lambda row: row.confidence_score, reverse=True)[:limit]


def _market_structure(selected: list[DirectionalEvaluationRecord]) -> dict[str, int]:
    states = Counter(str(row.payload.get("structure", "UNKNOWN")) for row in selected)
    return {
        "bullish": states["BULLISH"],
        "bearish": states["BEARISH"],
        "range": states["RANGE"],
        "unclear_or_degraded": sum(
            count for state, count in states.items() if state not in {"BULLISH", "BEARISH", "RANGE"}
        ),
        "long_compatible": sum(row.direction == "LONG" for row in selected),
        "short_compatible": sum(row.direction == "SHORT" for row in selected),
        "spot_buy_compatible": sum(row.direction == "BUY" for row in selected),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _in_window(value: datetime | None, cutoff: datetime) -> bool:
    return value is not None and _aware(value) >= cutoff
