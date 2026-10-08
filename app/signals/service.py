"""Persistent orchestration around the Phase 3A directional engine."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config.settings import Settings
from app.db.directional_models import DirectionalEvaluationRecord
from app.db.repositories.directional_diagnostics import DirectionalDiagnosticsRepository
from app.db.repositories.signals import SignalRepository
from app.db.signal_models import (
    SignalLifecycleEventRecord,
    SignalRecord,
    SignalTargetRecord,
    SignalUpdateRecord,
)
from app.market.analysis import MarketAnalysis
from app.signals.engine import DirectionalSignalEngine
from app.signals.identity import correlation_group_key, signal_fingerprint
from app.signals.lifecycle import validate_transition
from app.signals.models import DirectionalSignal, SignalClassification, SignalStatus


@dataclass(frozen=True)
class ServiceOutcome:
    signal: DirectionalSignal
    record: SignalRecord | None
    action: str


class SignalService:
    def __init__(
        self,
        repository: SignalRepository,
        settings: Settings,
        engine: DirectionalSignalEngine | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.engine = engine or DirectionalSignalEngine()

    async def evaluate(
        self,
        analysis: MarketAnalysis,
        *,
        btc_context: MarketAnalysis | None = None,
        evaluated_at: datetime | None = None,
        cycle_id: str | None = None,
    ) -> ServiceOutcome:
        now = evaluated_at or datetime.now(UTC)
        candidates = self.engine.evaluate_candidates(
            analysis, btc_context=btc_context, evaluated_at=now
        )
        signal = self.engine.select_candidate(analysis, candidates)
        outcome = await self.process(signal, evaluated_at=now)
        if cycle_id is not None:
            diagnostics = DirectionalDiagnosticsRepository(self.repository.session)
            await diagnostics.add_evaluations(
                [
                    self._evaluation_record(
                        cycle_id,
                        candidate,
                        selected=candidate.direction == signal.direction,
                        action=outcome.action
                        if candidate.direction == signal.direction
                        else "NOT_SELECTED",
                        evaluated_at=now,
                    )
                    for candidate in candidates
                ]
            )
            await self.repository.commit()
        return outcome

    async def process(
        self, signal: DirectionalSignal, *, evaluated_at: datetime | None = None
    ) -> ServiceOutcome:
        now = evaluated_at or datetime.now(UTC)
        if signal.classification is SignalClassification.NO_TRADE:
            invalidated = await self._invalidate_rejected_setup(signal, now)
            if invalidated is not None:
                return ServiceOutcome(signal, invalidated, "INVALIDATED")
            return ServiceOutcome(signal, None, "NO_TRADE")
        if signal.direction is None:
            return ServiceOutcome(signal, None, "NO_TRADE")
        fingerprint = signal_fingerprint(signal)
        existing = await self.repository.get_active_signal_by_fingerprint(fingerprint)
        if existing is None:
            latest = await self.repository.get_latest_by_fingerprint(fingerprint)
            if latest is not None:
                created_at = latest.created_at
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                cooldown = timedelta(seconds=self.settings.signal_deduplication_cooldown_seconds)
                if now - created_at < cooldown:
                    return ServiceOutcome(signal, latest, "DUPLICATE_SUPPRESSED")
            record = self._new_record(signal, fingerprint, now)
            await self.repository.create_signal(record)
            await self._event(
                record, None, SignalStatus.DISCOVERED, "Qualified setup discovered", now
            )
            target = self._desired_status(signal)
            await self.transition(record, target, f"Classified {signal.classification.value}", now)
            await self.repository.commit()
            return ServiceOutcome(signal, record, "CREATED")
        previous = existing.current_confidence
        existing.last_evaluated_at = now
        existing.analysis_timestamp = signal.analysis_timestamp
        existing.classification = signal.classification.value
        existing.current_confidence = signal.confidence_score
        existing.maximum_confidence = max(existing.maximum_confidence, signal.confidence_score)
        existing.minimum_confidence = min(existing.minimum_confidence, signal.confidence_score)
        existing.payload = signal.model_dump(mode="json")
        material = (
            abs(signal.confidence_score - previous) >= self.settings.signal_update_min_score_delta
        )
        if material:
            await self.repository.add_score_update(
                SignalUpdateRecord(
                    signal=existing,
                    previous_confidence=previous,
                    current_confidence=signal.confidence_score,
                    score_components=[
                        item.model_dump(mode="json") for item in signal.score_components
                    ],
                    reasons=list(signal.reasons),
                    warnings=list(signal.warnings),
                    at=now,
                )
            )
        if (
            existing.status == SignalStatus.WATCHLIST.value
            and self._desired_status(signal) is SignalStatus.WAITING_ENTRY
        ):
            await self.transition(
                existing,
                SignalStatus.WAITING_ENTRY,
                "Score and structure now meet actionable threshold",
                now,
            )
        await self.repository.commit()
        return ServiceOutcome(signal, existing, "UPDATED" if material else "DUPLICATE_SUPPRESSED")

    async def transition(
        self, record: SignalRecord, new: SignalStatus, reason: str, at: datetime
    ) -> None:
        previous = SignalStatus(record.status)
        validate_transition(previous, new)
        record.status = new.value
        if new is SignalStatus.EXPIRED:
            record.expired_at, record.expiry_reason = at, reason
        await self._event(record, previous, new, reason, at)

    async def expire_due(self, now: datetime) -> int:
        records = await self.repository.list_signals(
            statuses=[
                SignalStatus.WATCHLIST,
                SignalStatus.WAITING_ENTRY,
                SignalStatus.REVALIDATING,
            ],
            limit=1000,
        )
        count = 0
        for record in records:
            if record.expires_at is not None and _aware(record.expires_at) <= _aware(now):
                await self.transition(
                    record, SignalStatus.EXPIRED, "Setup maximum age exceeded", now
                )
                count += 1
        if count:
            await self.repository.commit()
        return count

    async def invalidate(self, record: SignalRecord, reason: str, at: datetime) -> None:
        await self.transition(record, SignalStatus.INVALIDATED, reason, at)
        await self.repository.commit()

    async def _invalidate_rejected_setup(
        self, signal: DirectionalSignal, now: datetime
    ) -> SignalRecord | None:
        if signal.direction is None:
            return None
        record = await self.repository.get_active_candidate(
            signal.exchange.value,
            signal.market_type.value,
            signal.symbol,
            signal.direction.value,
            signal.primary_timeframe.value,
        )
        if record is None:
            return None
        price = signal.supporting_metrics.get("current_price")
        crossed = (
            isinstance(price, (int, float))
            and record.invalidation_price is not None
            and (
                (signal.direction.bullish and price <= record.invalidation_price)
                or (not signal.direction.bullish and price >= record.invalidation_price)
            )
        )
        structural = any(
            phrase in reason.lower()
            for reason in signal.rejection_reasons
            for phrase in ("failed breakout", "already invalidated", "opposite structure")
        )
        if crossed or structural:
            reason = (
                "Structural invalidation crossed before entry"
                if crossed
                else signal.rejection_reasons[0]
            )
            await self.invalidate(record, reason, now)
            return record
        return None

    @staticmethod
    def _desired_status(signal: DirectionalSignal) -> SignalStatus:
        return (
            SignalStatus.WATCHLIST
            if signal.classification is SignalClassification.WATCHLIST
            else SignalStatus.WAITING_ENTRY
        )

    def _new_record(
        self, signal: DirectionalSignal, fingerprint: str, now: datetime
    ) -> SignalRecord:
        entry, invalidation = signal.entry_zone, signal.invalidation
        record = SignalRecord(
            id=str(uuid4()),
            fingerprint=fingerprint,
            correlation_group_key=correlation_group_key(signal),
            exchange=signal.exchange.value,
            market=signal.market_type.value,
            symbol=signal.symbol,
            direction=signal.direction.value if signal.direction else "NONE",
            setup_type=signal.signal_type.value,
            timeframe=signal.primary_timeframe.value,
            classification=signal.classification.value,
            status=SignalStatus.DISCOVERED.value,
            created_at=now,
            analysis_timestamp=signal.analysis_timestamp,
            last_evaluated_at=now,
            expires_at=signal.expires_at,
            waiting_since=now if signal.status is SignalStatus.WAITING_ENTRY else None,
            initial_confidence=signal.confidence_score,
            current_confidence=signal.confidence_score,
            maximum_confidence=signal.confidence_score,
            minimum_confidence=signal.confidence_score,
            entry_low=entry.low if entry else None,
            entry_high=entry.high if entry else None,
            ideal_entry=signal.ideal_entry,
            entry_basis=entry.entry_type.value if entry else None,
            entry_reason=entry.reason if entry else None,
            invalidation_price=invalidation.price if invalidation else None,
            original_invalidation_price=invalidation.price if invalidation else None,
            monitoring_degraded=False,
            weakening_reasons=[],
            payload=signal.model_dump(mode="json"),
        )
        record.targets = [
            SignalTargetRecord(ordinal=i, **target.model_dump())
            for i, target in enumerate(signal.targets, 1)
        ]
        record.monitoring_events = []
        return record

    async def _event(
        self,
        record: SignalRecord,
        previous: SignalStatus | None,
        new: SignalStatus,
        reason: str,
        at: datetime,
    ) -> None:
        await self.repository.add_lifecycle_event(
            SignalLifecycleEventRecord(
                signal=record,
                previous_status=previous.value if previous else None,
                new_status=new.value,
                reason=reason,
                at=at,
            )
        )

    @staticmethod
    def _evaluation_record(
        cycle_id: str,
        signal: DirectionalSignal,
        *,
        selected: bool,
        action: str,
        evaluated_at: datetime,
    ) -> DirectionalEvaluationRecord:
        reasons = list(signal.rejection_reasons)
        diagnostic_score = signal.supporting_metrics.get("diagnostic_score_before_hard_rejection")
        score = (
            float(diagnostic_score)
            if isinstance(diagnostic_score, (int, float))
            else signal.confidence_score
        )
        return DirectionalEvaluationRecord(
            cycle_id=cycle_id,
            evaluated_at=evaluated_at,
            analysis_timestamp=signal.analysis_timestamp,
            exchange=signal.exchange.value,
            market=signal.market_type.value,
            symbol=signal.symbol,
            direction=signal.direction.value if signal.direction else "NONE",
            selected=selected,
            classification=signal.classification.value,
            final_state=signal.status.value,
            persistence_action=action,
            confidence_score=score,
            scorable=bool(signal.score_components),
            primary_rejection_reason=reasons[0] if reasons else None,
            rejection_reasons=reasons,
            score_components=[row.model_dump(mode="json") for row in signal.score_components],
            payload=signal.model_dump(mode="json"),
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
