"""Transparent current-signal ranking and cross-venue grouping."""

from datetime import UTC, datetime
from typing import Any

from app.db.signal_models import SignalRecord


def ranking_score(record: SignalRecord, now: datetime | None = None) -> float:
    current = now or datetime.now(UTC)
    evaluated_at = record.last_evaluated_at
    if evaluated_at.tzinfo is None:  # SQLite does not preserve timezone offsets.
        evaluated_at = evaluated_at.replace(tzinfo=UTC)
    age_hours = max(0.0, (current - evaluated_at).total_seconds() / 3600)
    payload = record.payload
    best_r = float((payload.get("risk_reward") or {}).get("best_r", 0))
    entry_score = next(
        (float(x["score"]) for x in payload.get("score_components", []) if x["name"] == "entry"), 0
    )
    return record.current_confidence + min(best_r, 5) + entry_score * 0.2 - min(age_hours, 10)


def group_top(records: list[SignalRecord], limit: int = 5) -> list[dict[str, Any]]:
    grouped: dict[str, list[SignalRecord]] = {}
    for record in records:
        # Deliberately omit venue: equivalent ideas remain persisted but share one display slot.
        key = f"{record.symbol}:{record.direction}:{record.market}:{record.timeframe}"
        grouped.setdefault(key, []).append(record)
    representatives: list[dict[str, Any]] = []
    for alternatives in grouped.values():
        ordered = sorted(
            alternatives, key=lambda item: (-ranking_score(item), item.exchange, item.id)
        )
        representatives.append(
            {
                "signal": ordered[0],
                "ranking_score": ranking_score(ordered[0]),
                "alternative_venues": [
                    {"signal_id": x.id, "exchange": x.exchange, "confidence": x.current_confidence}
                    for x in ordered[1:]
                ],
            }
        )
    return sorted(representatives, key=lambda x: (-float(x["ranking_score"]), x["signal"].id))[
        :limit
    ]
