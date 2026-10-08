"""Persistent opportunity deduplication and qualification service."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.arbitrage.identity import opportunity_fingerprint
from app.arbitrage.models import ArbitrageStatus, ArbitrageType
from app.arbitrage.scanner import ArbitrageCandidate
from app.arbitrage.scoring import score_opportunity
from app.config.settings import Settings
from app.db.arbitrage_models import (
    ArbitrageLifecycleEventRecord,
    ArbitrageOpportunityRecord,
    ArbitrageUpdateRecord,
)
from app.db.repositories.arbitrage import ArbitrageRepository


class ArbitrageService:
    def __init__(self, repository: ArbitrageRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings

    async def process(
        self, candidate: ArbitrageCandidate, at: datetime
    ) -> tuple[ArbitrageOpportunityRecord, bool]:
        kind = (
            ArbitrageType.CROSS_EXCHANGE_SPOT
            if candidate.market.value == "SPOT"
            else ArbitrageType.CROSS_EXCHANGE_FUTURES
        )
        fingerprint = opportunity_fingerprint(
            kind,
            candidate.symbol,
            candidate.buy_exchange,
            candidate.market,
            candidate.sell_exchange,
            candidate.market,
        )
        row = await self.repository.active_by_fingerprint(fingerprint)
        detected_at = None if row is None else self._utc(row.detected_at)
        score, components, classification = score_opportunity(
            net_edge_pct=candidate.economics.expected_net_edge_pct,
            liquidity_ratio=Decimal("1"),
            persistence_ms=Decimal("0")
            if detected_at is None
            else Decimal(str((at - detected_at).total_seconds() * 1000)),
            quote_age_ms=max(candidate.buy_age_ms, candidate.sell_age_ms),
            timestamp_skew_ms=candidate.timestamp_skew_ms,
            spread_instability=Decimal("0"),
            slippage_pct=candidate.economics.slippage_pct,
            settings=self.settings,
        )
        payload = {
            "economics": candidate.economics.model_dump(mode="json"),
            "score_components": [item.model_dump(mode="json") for item in components],
        }
        if row is None:
            row = ArbitrageOpportunityRecord(
                id=str(uuid4()),
                fingerprint=fingerprint,
                arbitrage_type=kind,
                symbol=candidate.symbol,
                buy_exchange=candidate.buy_exchange,
                buy_market=candidate.market,
                sell_exchange=candidate.sell_exchange,
                sell_market=candidate.market,
                status=ArbitrageStatus.DETECTED,
                classification=classification,
                detected_at=at,
                last_updated_at=at,
                consecutive_updates=1,
                confidence_score=float(score),
                gross_spread_pct=candidate.economics.gross_spread_pct,
                net_edge_pct=candidate.economics.expected_net_edge_pct,
                expected_net_pnl=candidate.economics.expected_net_pnl,
                target_notional=candidate.economics.target_notional,
                payload=payload,
            )
            row.events.append(
                ArbitrageLifecycleEventRecord(
                    event_key=f"{row.id}:DETECTED",
                    previous_status=None,
                    new_status=ArbitrageStatus.DETECTED,
                    reason="Executable net edge detected",
                    at=at,
                )
            )
            await self.repository.add(row)
            await self.repository.commit()
            return row, True
        old_edge = Decimal(str(row.net_edge_pct))
        row.consecutive_updates += 1
        assert detected_at is not None
        elapsed_ms = (at - detected_at).total_seconds() * 1000
        previous = row.status
        if (
            row.consecutive_updates >= self.settings.arbitrage_min_consecutive_updates
            and elapsed_ms >= self.settings.arbitrage_min_persistence_ms
            and classification.value in {"STRONG_OPPORTUNITY", "VERY_STRONG_OPPORTUNITY"}
        ):
            row.status = ArbitrageStatus.QUALIFIED
        else:
            row.status = ArbitrageStatus.PERSISTING
        row.classification = classification
        row.last_updated_at = at
        row.confidence_score = float(score)
        row.net_edge_pct = candidate.economics.expected_net_edge_pct
        row.gross_spread_pct = candidate.economics.gross_spread_pct
        row.expected_net_pnl = candidate.economics.expected_net_pnl
        row.payload = payload
        if previous != row.status:
            row.events.append(
                ArbitrageLifecycleEventRecord(
                    event_key=f"{row.id}:{row.status}:{row.consecutive_updates}",
                    previous_status=previous,
                    new_status=row.status,
                    reason="Persistence and quality criteria reevaluated",
                    at=at,
                )
            )
        if abs(candidate.economics.expected_net_edge_pct - old_edge) * Decimal("100") >= Decimal(
            str(self.settings.arbitrage_update_min_edge_delta_bps)
        ):
            row.updates.append(
                ArbitrageUpdateRecord(
                    previous_net_edge_pct=old_edge,
                    current_net_edge_pct=candidate.economics.expected_net_edge_pct,
                    confidence_score=float(score),
                    at=at,
                    payload=payload,
                )
            )
        await self.repository.commit()
        return row, False

    @staticmethod
    def _utc(value: datetime) -> datetime:
        """SQLite can return a timezone-naive value for a timezone-aware column."""
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    async def invalidate(self, row: ArbitrageOpportunityRecord, at: datetime, reason: str) -> None:
        if row.status in {
            ArbitrageStatus.INVALIDATED,
            ArbitrageStatus.EXPIRED,
            ArbitrageStatus.CLOSED,
        }:
            return
        previous = row.status
        row.status = ArbitrageStatus.INVALIDATED
        row.events.append(
            ArbitrageLifecycleEventRecord(
                event_key=f"{row.id}:INVALIDATED",
                previous_status=previous,
                new_status=ArbitrageStatus.INVALIDATED,
                reason=reason,
                at=at,
            )
        )
        await self.repository.commit()
