"""Pending-entry calibration metrics derived from persisted paper-only lifecycle data."""

from datetime import UTC, datetime
from statistics import fmean

from app.db.portfolio_models import PaperPositionRecord
from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord


def pending_metrics(
    signals: list[SignalRecord], positions: list[PaperPositionRecord]
) -> dict[str, object]:
    pending = [row for row in signals if row.waiting_since is not None]
    entered = [row for row in pending if row.entry_confirmed_at is not None]
    events = [event for row in pending for event in row.monitoring_events]
    position_by_signal = {row.signal_id: row for row in positions}
    r_results: list[float] = []
    for row in entered:
        position = position_by_signal.get(row.id)
        if position is None:
            continue
        risk = (
            abs(position.simulated_fill_price - position.initial_stop) * position.initial_quantity
        )
        if risk:
            r_results.append(float(position.realized_pnl / risk))
    waits = [
        (_aware(row.entry_confirmed_at) - _aware(row.waiting_since)).total_seconds()
        for row in entered
        if row.entry_confirmed_at is not None and row.waiting_since is not None
    ]
    deltas = [
        row.confidence_delta_at_entry
        for row in entered
        if row.confidence_delta_at_entry is not None
    ]
    return {
        "pending_setups_created": len(pending),
        "entry_zones_reached": sum(row.entry_zone_reached_at is not None for row in pending),
        "revalidation_passes": len(entered),
        "revalidation_failures": sum(e.event_type == "REVALIDATION_FAILED" for e in events),
        "expired_before_entry": sum(row.status == "EXPIRED" for row in pending),
        "structurally_invalidated": sum(row.status == "INVALIDATED" for row in pending),
        "paper_entries_opened": sum(row.id in position_by_signal for row in entered),
        "tp1_rate": _rate(events, "TP1_REACHED", len(entered)),
        "tp2_rate": _rate(events, "TP2_REACHED", len(entered)),
        "tp3_rate": _rate(events, "TP3_REACHED", len(entered)),
        "stop_rate": _rate(events, "STOP_REACHED", len(entered)),
        "average_r": fmean(r_results) if r_results else 0.0,
        "expectancy_r": fmean(r_results) if r_results else 0.0,
        "average_waiting_seconds": fmean(waits) if waits else 0.0,
        "average_confidence_change": fmean(deltas) if deltas else 0.0,
        "entry_origin": {
            "immediate": sum(
                row.payload.get("entry_origin") == "ACTIONABLE_NOW" for row in entered
            ),
            "waiting": sum(row.payload.get("entry_origin") != "ACTIONABLE_NOW" for row in entered),
        },
    }


def _rate(events: list[SignalMonitoringEventRecord], kind: str, denominator: int) -> float:
    count = sum(event.event_type == kind for event in events)
    return count / denominator if denominator else 0.0


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
