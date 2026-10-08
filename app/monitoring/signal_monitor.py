"""Idempotent tick-driven lifecycle monitoring."""

import hashlib
from datetime import UTC, datetime

from app.config.settings import Settings
from app.db.repositories.signals import SignalRepository
from app.db.signal_models import SignalMonitoringEventRecord, SignalRecord, SignalTargetRecord
from app.monitoring.events import MonitoringEventType
from app.monitoring.models import CandlePriceEvent, MonitorResult, PriceEvent
from app.monitoring.policies import StopManagementPolicy, breakeven_stop, structure_trailing_stop
from app.signals.lifecycle import validate_transition
from app.signals.models import (
    DirectionalSignal,
    SignalClassification,
    SignalDirection,
    SignalStatus,
)

TERMINAL = {
    SignalStatus.STOPPED,
    SignalStatus.COMPLETED,
    SignalStatus.CLOSED,
    SignalStatus.INVALIDATED,
    SignalStatus.EXPIRED,
    SignalStatus.REJECTED,
}


class SignalMonitor:
    """Apply ordered public-data events without executing orders."""

    def __init__(self, repository: SignalRepository, settings: Settings) -> None:
        self.repository = repository
        self.settings = settings
        self.policy = StopManagementPolicy(settings.stop_management_policy.upper())

    async def process_price(self, record: SignalRecord, event: PriceEvent) -> MonitorResult:
        previous = record.status
        if event.symbol != record.symbol:
            return self._ignored(record, previous, "symbol mismatch")
        event_at = _aware(event.exchange_timestamp)
        last_at = _aware(record.last_market_event_at) if record.last_market_event_at else None
        if last_at is not None and event_at < last_at:
            return self._ignored(record, previous, "out-of-order event")
        if event.data_age_ms > self.settings.monitoring_stale_quote_ms:
            stale_events = await self._degrade(record, event)
            await self.repository.commit()
            return MonitorResult(
                signal_id=record.id,
                previous_status=previous,
                current_status=record.status,
                events=tuple(stale_events),
                ignored_reason="stale market event",
            )
        emitted: list[str] = []
        if record.monitoring_degraded:
            record.monitoring_degraded = False
            if await self._emit(
                record,
                MonitoringEventType.MONITORING_RECOVERED,
                event,
                reasons=["Fresh public market data resumed"],
            ):
                emitted.append(MonitoringEventType.MONITORING_RECOVERED.value)
        record.last_market_event_at = event_at
        record.current_monitored_price = event.last
        record.current_bid_price = event.bid
        record.current_ask_price = event.ask
        status = SignalStatus(record.status)
        if status in TERMINAL:
            await self.repository.commit()
            return self._ignored(record, previous, "terminal signal")
        if status is SignalStatus.WAITING_ENTRY:
            entry_price = self._entry_price(record, event)
            record.current_executable_price = entry_price
            expires_at = _aware(record.expires_at) if record.expires_at else None
            if expires_at is not None and event_at >= expires_at:
                record.expired_at = event_at
                record.expiry_reason = "Entry zone was not reached before setup expiry"
                await self._transition(
                    record,
                    SignalStatus.EXPIRED,
                    record.expiry_reason,
                    event,
                    MonitoringEventType.SETUP_EXPIRED,
                )
                emitted.append(MonitoringEventType.SETUP_EXPIRED.value)
            elif self._stop_crossed(record, entry_price):
                reason = "Structural invalidation level broke before paper entry"
                await self._transition(
                    record,
                    SignalStatus.INVALIDATED,
                    reason,
                    event,
                    MonitoringEventType.SETUP_INVALIDATED,
                    observed=entry_price,
                    configured=record.invalidation_price,
                )
                emitted.append(MonitoringEventType.SETUP_INVALIDATED.value)
            elif not self._inside_entry(record, entry_price):
                distance = self._distance_to_entry_atr(record, entry_price)
                if (
                    distance is not None
                    and distance <= self.settings.entry_approaching_distance_atr
                ):
                    created = await self._emit(
                        record,
                        MonitoringEventType.ENTRY_APPROACHING,
                        event,
                        reasons=[f"Executable quote is {distance:.2f} ATR from entry zone"],
                        metadata={"distance_atr": distance},
                        stable_key="entry-approaching",
                    )
                    if created:
                        emitted.append(MonitoringEventType.ENTRY_APPROACHING.value)
            elif event.bid is None or event.ask is None:
                await self.repository.commit()
                return self._ignored(record, previous, "two-sided quote required for revalidation")
            elif self._spread_bps(event) > self.settings.max_entry_spread_bps:
                await self.repository.commit()
                return self._ignored(record, previous, "entry spread exceeds configured maximum")
            else:
                record.entry_zone_reached_at = event_at
                record.revalidation_started_at = event_at
                await self._transition(
                    record,
                    SignalStatus.REVALIDATING,
                    "Executable quote entered zone; complete setup revalidation required",
                    event,
                    MonitoringEventType.REVALIDATION_STARTED,
                    observed=entry_price,
                )
                emitted.append(MonitoringEventType.REVALIDATION_STARTED.value)
            await self.repository.commit()
            return MonitorResult(
                signal_id=record.id,
                previous_status=previous,
                current_status=record.status,
                events=tuple(emitted),
            )
        if status is SignalStatus.REVALIDATING:
            await self.repository.commit()
            return self._ignored(record, previous, "revalidation already in progress")
        # With an ordered tick there is one observed price. Stop is conservative if levels overlap.
        if self._stop_crossed(record, event.last):
            record.stopped_at = event_at
            await self._transition(
                record,
                SignalStatus.STOPPED,
                "Structural invalidation reached",
                event,
                MonitoringEventType.STOP_REACHED,
                observed=event.last,
                configured=record.invalidation_price,
            )
            emitted.append(MonitoringEventType.STOP_REACHED.value)
        else:
            emitted.extend(await self._targets(record, event))
        await self.repository.commit()
        return MonitorResult(
            signal_id=record.id,
            previous_status=previous,
            current_status=record.status,
            events=tuple(emitted),
        )

    async def complete_entry_revalidation(
        self, record: SignalRecord, signal: DirectionalSignal, event: PriceEvent
    ) -> MonitorResult:
        previous = record.status
        if SignalStatus(record.status) is not SignalStatus.REVALIDATING:
            return self._ignored(record, previous, "signal is not revalidating")
        executable = self._entry_price(record, event)
        failures = self._revalidation_failures(record, signal, event, executable)
        if failures:
            record.revalidated_at = _aware(event.received_at)
            record.revalidation_reason = "; ".join(failures)
            await self._transition(
                record,
                SignalStatus.INVALIDATED,
                record.revalidation_reason,
                event,
                MonitoringEventType.REVALIDATION_FAILED,
                observed=executable,
            )
            await self.repository.commit()
            return MonitorResult(
                signal_id=record.id,
                previous_status=previous,
                current_status=record.status,
                events=(MonitoringEventType.REVALIDATION_FAILED.value,),
            )
        assert signal.invalidation is not None and signal.risk_reward is not None
        record.revalidated_at = _aware(event.received_at)
        record.revalidation_reason = "Complete setup revalidation passed"
        record.current_confidence = signal.confidence_score
        record.confidence_at_entry = signal.confidence_score
        record.confidence_delta_at_entry = signal.confidence_score - record.initial_confidence
        record.max_confidence_after_entry = signal.confidence_score
        record.min_confidence_after_entry = signal.confidence_score
        record.entry_confirmed_at = _aware(event.exchange_timestamp)
        record.entry_price_reference = executable
        record.entry_confirmation_type = "ASK_IN_ZONE" if self._bullish(record) else "BID_IN_ZONE"
        record.invalidation_price = signal.invalidation.price
        payload = signal.model_dump(mode="json")
        payload["risk_reward_at_entry"] = self._current_best_r(record, signal, executable)
        payload["entry_origin"] = record.payload.get("supporting_metrics", {}).get(
            "entry_zone_state", "PENDING_ENTRY"
        )
        record.payload = payload
        record.targets.clear()
        record.targets.extend(
            SignalTargetRecord(ordinal=index, **target.model_dump())
            for index, target in enumerate(signal.targets, 1)
        )
        await self._transition(
            record,
            SignalStatus.ENTRY_CONFIRMED,
            "Fresh analysis passed all pending-entry confirmation rules",
            event,
            MonitoringEventType.ENTRY_CONFIRMED,
            observed=executable,
            configured=signal.ideal_entry,
        )
        await self._transition(
            record,
            SignalStatus.ACTIVE,
            "Confirmed paper entry activated existing realtime lifecycle",
            event,
            MonitoringEventType.SIGNAL_ACTIVE,
            observed=executable,
        )
        await self.repository.commit()
        return MonitorResult(
            signal_id=record.id,
            previous_status=previous,
            current_status=record.status,
            events=(
                MonitoringEventType.ENTRY_CONFIRMED.value,
                MonitoringEventType.SIGNAL_ACTIVE.value,
            ),
        )

    async def defer_entry_revalidation(
        self, record: SignalRecord, event: PriceEvent, reason: str
    ) -> MonitorResult:
        previous = record.status
        if SignalStatus(record.status) is SignalStatus.REVALIDATING:
            await self._transition(
                record,
                SignalStatus.WAITING_ENTRY,
                reason,
                event,
                MonitoringEventType.MONITORING_DEGRADED,
            )
            record.monitoring_degraded = True
            await self.repository.commit()
        return MonitorResult(
            signal_id=record.id,
            previous_status=previous,
            current_status=record.status,
            events=(MonitoringEventType.MONITORING_DEGRADED.value,),
        )

    async def process_candle(self, record: SignalRecord, candle: CandlePriceEvent) -> MonitorResult:
        previous = record.status
        if SignalStatus(record.status) in TERMINAL | {SignalStatus.WAITING_ENTRY}:
            return self._ignored(record, previous, "candle fallback inactive")
        target, stop = self._next_target(record), record.invalidation_price
        if target is not None and stop is not None:
            target_touched = (
                candle.high >= target.price if self._bullish(record) else candle.low <= target.price
            )
            stop_touched = candle.low <= stop if self._bullish(record) else candle.high >= stop
            if target_touched and stop_touched:
                pseudo = PriceEvent(
                    symbol=record.symbol,
                    last=candle.close,
                    exchange_timestamp=candle.exchange_timestamp,
                    received_at=candle.received_at,
                    source="CANDLE",
                )
                await self._emit(
                    record,
                    MonitoringEventType.AMBIGUOUS_INTRABAR,
                    pseudo,
                    reasons=["Fallback candle touched both next target and stop; path is unknown"],
                    warnings=["No favorable event order assumed"],
                )
                await self.repository.commit()
                return MonitorResult(
                    signal_id=record.id,
                    previous_status=previous,
                    current_status=record.status,
                    events=(MonitoringEventType.AMBIGUOUS_INTRABAR.value,),
                    ignored_reason="ambiguous intrabar path",
                )
        return self._ignored(record, previous, "no unambiguous candle trigger")

    async def refresh_confidence(
        self, record: SignalRecord, signal: DirectionalSignal, at: datetime
    ) -> MonitorResult:
        previous_status, previous_score = record.status, record.current_confidence
        record.current_confidence = signal.confidence_score
        record.maximum_confidence = max(record.maximum_confidence, signal.confidence_score)
        record.minimum_confidence = min(record.minimum_confidence, signal.confidence_score)
        record.max_confidence_after_entry = max(
            record.max_confidence_after_entry or signal.confidence_score, signal.confidence_score
        )
        record.min_confidence_after_entry = min(
            record.min_confidence_after_entry or signal.confidence_score, signal.confidence_score
        )
        old = {x["name"]: float(x["score"]) for x in record.payload.get("score_components", [])}
        watched = {"structure", "momentum", "volume", "btc_context", "volatility"}
        deteriorated = [
            x.name
            for x in signal.score_components
            if x.name in watched and old.get(x.name, x.score) - x.score >= 1
        ]
        decline = previous_score - signal.confidence_score
        event = PriceEvent(
            symbol=record.symbol,
            last=record.current_monitored_price or signal.ideal_entry or 1,
            exchange_timestamp=at,
            received_at=at,
        )
        emitted: list[str] = []
        if abs(decline) >= self.settings.signal_update_min_score_delta:
            if await self._emit(
                record,
                MonitoringEventType.CONFIDENCE_UPDATED,
                event,
                previous_confidence=previous_score,
                current_confidence=signal.confidence_score,
                reasons=list(signal.reasons),
                warnings=list(signal.warnings),
                analysis_timestamp=signal.analysis_timestamp,
            ):
                emitted.append(MonitoringEventType.CONFIDENCE_UPDATED.value)
        current = SignalStatus(record.status)
        if current is SignalStatus.WAITING_ENTRY:
            pending_failures = self._pending_refresh_failures(record, signal)
            if pending_failures:
                reason = "; ".join(pending_failures)
                record.revalidation_reason = reason
                await self._transition(
                    record,
                    SignalStatus.INVALIDATED,
                    reason,
                    event,
                    MonitoringEventType.SETUP_INVALIDATED,
                )
                emitted.append(MonitoringEventType.SETUP_INVALIDATED.value)
            prior = record.payload.get("pre_weakening_status")
            record.payload = signal.model_dump(mode="json") | {"pre_weakening_status": prior}
            await self.repository.commit()
            return MonitorResult(
                signal_id=record.id,
                previous_status=previous_status,
                current_status=record.status,
                events=tuple(emitted),
            )
        if (
            decline >= self.settings.weakening_score_delta
            and len(deteriorated) >= self.settings.weakening_min_conditions
            and current not in TERMINAL | {SignalStatus.WEAKENING}
            and self._weakening_cooldown_ready(record, at)
        ):
            payload = dict(record.payload)
            payload["pre_weakening_status"] = record.status
            record.payload = payload
            record.weakening_reasons = [f"{name} score deteriorated" for name in deteriorated]
            await self._transition(
                record,
                SignalStatus.WEAKENING,
                "; ".join(record.weakening_reasons),
                event,
                MonitoringEventType.SIGNAL_WEAKENING,
            )
            emitted.append(MonitoringEventType.SIGNAL_WEAKENING.value)
        elif (
            current is SignalStatus.WEAKENING
            and (signal.confidence_score >= previous_score + self.settings.weakening_hysteresis)
            and self._weakening_cooldown_ready(record, at)
        ):
            recovered = SignalStatus(record.payload.get("pre_weakening_status", "ACTIVE"))
            if recovered not in {
                SignalStatus.ACTIVE,
                SignalStatus.TP1_REACHED,
                SignalStatus.TP2_REACHED,
            }:
                recovered = SignalStatus.ACTIVE
            record.weakening_reasons = []
            await self._transition(
                record,
                recovered,
                "Strategy conditions recovered",
                event,
                MonitoringEventType.SIGNAL_RECOVERED,
            )
            emitted.append(MonitoringEventType.SIGNAL_RECOVERED.value)
        prior = record.payload.get("pre_weakening_status")
        record.payload = signal.model_dump(mode="json") | {"pre_weakening_status": prior}
        await self.repository.commit()
        return MonitorResult(
            signal_id=record.id,
            previous_status=previous_status,
            current_status=record.status,
            events=tuple(emitted),
        )

    async def apply_confirmed_structure_trail(
        self, record: SignalRecord, swing_price: float, atr_buffer: float, at: datetime
    ) -> bool:
        if (
            self.policy is not StopManagementPolicy.STRUCTURE_TRAILING
            or record.invalidation_price is None
        ):
            return False
        candidate = structure_trailing_stop(
            record.invalidation_price, swing_price, self._bullish(record), atr_buffer
        )
        if candidate is None:
            return False
        old = record.invalidation_price
        record.invalidation_price = candidate
        event = PriceEvent(
            symbol=record.symbol,
            last=record.current_monitored_price or candidate,
            exchange_timestamp=at,
            received_at=at,
        )
        await self._emit(
            record,
            MonitoringEventType.STOP_UPDATED,
            event,
            configured=candidate,
            reasons=[f"Confirmed structure advanced stop from {old} to {candidate}"],
            metadata={"old_stop": old, "policy": self.policy.value},
        )
        await self.repository.commit()
        return True

    async def close(self, record: SignalRecord, reason: str, at: datetime) -> None:
        event = PriceEvent(
            symbol=record.symbol,
            last=record.current_monitored_price or 1,
            exchange_timestamp=at,
            received_at=at,
        )
        record.closed_at = at
        await self._transition(
            record, SignalStatus.CLOSED, reason, event, MonitoringEventType.SIGNAL_CLOSED
        )
        await self.repository.commit()

    async def _targets(self, record: SignalRecord, event: PriceEvent) -> list[str]:
        emitted: list[str] = []
        reached = {x.event_type for x in record.monitoring_events}
        for target in record.targets:
            kind = MonitoringEventType(f"{target.name}_REACHED")
            if kind.value in reached:
                continue
            crossed = (
                event.last >= target.price if self._bullish(record) else event.last <= target.price
            )
            if not crossed:
                break
            await self._transition(
                record,
                SignalStatus(kind.value),
                f"{target.name} reached",
                event,
                kind,
                observed=event.last,
                configured=target.price,
            )
            emitted.append(kind.value)
            if target.ordinal == 1:
                emitted.extend(await self._manage_stop_after_tp1(record, event))
        reached.update(emitted)
        if record.targets and all(f"TP{x.ordinal}_REACHED" in reached for x in record.targets):
            record.completed_at = event.exchange_timestamp
            await self._transition(
                record,
                SignalStatus.COMPLETED,
                "Final configured target reached",
                event,
                MonitoringEventType.SIGNAL_COMPLETED,
            )
            emitted.append(MonitoringEventType.SIGNAL_COMPLETED.value)
        return emitted

    async def _manage_stop_after_tp1(self, record: SignalRecord, event: PriceEvent) -> list[str]:
        if (
            self.policy is not StopManagementPolicy.BREAKEVEN_AFTER_TP1
            or record.entry_price_reference is None
        ):
            return []
        old = record.invalidation_price
        new = breakeven_stop(
            record.entry_price_reference, self._bullish(record), self.settings.breakeven_buffer_bps
        )
        record.invalidation_price = new
        await self._emit(
            record,
            MonitoringEventType.STOP_UPDATED,
            event,
            configured=new,
            reasons=["Configured breakeven-after-TP1 policy applied"],
            metadata={"old_stop": old, "policy": self.policy.value},
        )
        return [MonitoringEventType.STOP_UPDATED.value]

    async def _transition(
        self,
        record: SignalRecord,
        new: SignalStatus,
        reason: str,
        event: PriceEvent,
        kind: MonitoringEventType,
        *,
        observed: float | None = None,
        configured: float | None = None,
    ) -> None:
        previous = SignalStatus(record.status)
        validate_transition(previous, new)
        record.status = new.value
        await self._emit(
            record,
            kind,
            event,
            previous_status=previous,
            new_status=new,
            reasons=[reason],
            observed=observed,
            configured=configured,
        )

    async def _emit(
        self,
        record: SignalRecord,
        kind: MonitoringEventType,
        event: PriceEvent,
        *,
        previous_status: SignalStatus | None = None,
        new_status: SignalStatus | None = None,
        reasons: list[str] | None = None,
        warnings: list[str] | None = None,
        observed: float | None = None,
        configured: float | None = None,
        previous_confidence: float | None = None,
        current_confidence: float | None = None,
        analysis_timestamp: datetime | None = None,
        metadata: dict[str, object] | None = None,
        stable_key: str | None = None,
    ) -> bool:
        raw = f"{record.id}:{kind.value}:{stable_key or event.exchange_timestamp.isoformat()}"
        key = hashlib.sha256(raw.encode()).hexdigest()
        if await self.repository.has_monitoring_event(key):
            return False
        price = observed if observed is not None else event.last
        await self.repository.add_monitoring_event(
            SignalMonitoringEventRecord(
                signal=record,
                event_key=key,
                event_type=kind.value,
                previous_status=previous_status.value if previous_status else None,
                new_status=new_status.value if new_status else None,
                previous_confidence=previous_confidence,
                current_confidence=current_confidence,
                price=price,
                configured_price=configured,
                slippage=(price - configured) if configured is not None else None,
                reasons=reasons or [],
                warnings=warnings or [],
                analysis_timestamp=analysis_timestamp,
                exchange_timestamp=event.exchange_timestamp,
                received_at=event.received_at,
                data_age_ms=event.data_age_ms,
                metadata_json=metadata or {},
            )
        )
        return True

    async def _degrade(self, record: SignalRecord, event: PriceEvent) -> list[str]:
        if record.monitoring_degraded:
            return []
        record.monitoring_degraded = True
        created = await self._emit(
            record,
            MonitoringEventType.MONITORING_DEGRADED,
            event,
            reasons=[f"Quote age {event.data_age_ms:.0f}ms exceeds configured maximum"],
            warnings=["No entry, target, or stop trigger evaluated"],
        )
        return [MonitoringEventType.MONITORING_DEGRADED.value] if created else []

    def _entry_price(self, record: SignalRecord, event: PriceEvent) -> float:
        return (event.ask or event.last) if self._bullish(record) else (event.bid or event.last)

    @staticmethod
    def _spread_bps(event: PriceEvent) -> float:
        if event.bid is None or event.ask is None:
            return float("inf")
        midpoint = (event.bid + event.ask) / 2
        return (event.ask - event.bid) / midpoint * 10_000 if midpoint else float("inf")

    @staticmethod
    def _distance_to_entry_atr(record: SignalRecord, price: float) -> float | None:
        atr = record.payload.get("supporting_metrics", {}).get("atr")
        if not isinstance(atr, (int, float)) or atr <= 0:
            return None
        if record.entry_low is None or record.entry_high is None:
            return None
        if price < record.entry_low:
            return (record.entry_low - price) / atr
        if price > record.entry_high:
            return (price - record.entry_high) / atr
        return 0.0

    def _revalidation_failures(
        self,
        record: SignalRecord,
        signal: DirectionalSignal,
        event: PriceEvent,
        executable: float,
    ) -> list[str]:
        failures: list[str] = []
        if event.data_age_ms > self.settings.max_entry_age_seconds * 1000:
            failures.append("market quote became stale before confirmation")
        if event.bid is None or event.ask is None:
            failures.append("two-sided executable quote unavailable")
        elif self._spread_bps(event) > self.settings.max_entry_spread_bps:
            failures.append("spread exceeds entry execution-quality limit")
        if not self._inside_entry(record, executable):
            failures.append("executable quote left the original entry zone")
        if _aware(signal.analysis_timestamp) > _aware(event.received_at):
            failures.append("analysis timestamp is later than the entry event")
        if signal.direction is None or signal.direction.value != record.direction:
            failures.append("revalidated direction no longer matches pending setup")
        if signal.classification not in {
            SignalClassification.STRONG_SETUP,
            SignalClassification.VERY_STRONG_SETUP,
        }:
            failures.append("revalidated setup is no longer strongly qualified")
        if signal.confidence_score < self.settings.entry_confirm_min_score:
            failures.append("current confidence is below entry confirmation threshold")
        confidence_drop = record.initial_confidence - signal.confidence_score
        if confidence_drop > self.settings.max_confidence_drop_before_entry:
            failures.append("confidence deteriorated beyond the permitted entry delta")
        if signal.data_quality != "VALID":
            failures.append("revalidation market data quality is degraded")
        if signal.rejection_reasons:
            failures.extend(signal.rejection_reasons)
        if signal.invalidation is None:
            failures.append("revalidated stop is unavailable")
        elif (self._bullish(record) and executable <= signal.invalidation.price) or (
            not self._bullish(record) and executable >= signal.invalidation.price
        ):
            failures.append("revalidated stop is structurally invalid")
        if not signal.targets:
            failures.append("revalidated target structure is unavailable")
        if (
            signal.risk_reward is None
            or signal.risk_reward.best_r < self.settings.minimum_acceptable_r
        ):
            failures.append("revalidated projected risk/reward is below minimum")
        current_best_r = self._current_best_r(record, signal, executable)
        if current_best_r is None or current_best_r < self.settings.minimum_acceptable_r:
            failures.append("current executable-price risk/reward is below minimum")
        return list(dict.fromkeys(failures))

    def _current_best_r(
        self, record: SignalRecord, signal: DirectionalSignal, executable: float
    ) -> float | None:
        if signal.invalidation is None or not signal.targets:
            return None
        risk = (
            executable - signal.invalidation.price
            if self._bullish(record)
            else signal.invalidation.price - executable
        )
        if risk <= 0:
            return None
        rewards = [
            target.price - executable if self._bullish(record) else executable - target.price
            for target in signal.targets
        ]
        valid = [reward / risk for reward in rewards if reward > 0]
        return max(valid) if valid else None

    def _pending_refresh_failures(
        self, record: SignalRecord, signal: DirectionalSignal
    ) -> list[str]:
        failures: list[str] = []
        if signal.direction is None or signal.direction.value != record.direction:
            failures.append("direction or higher-timeframe alignment changed")
        if signal.classification not in {
            SignalClassification.STRONG_SETUP,
            SignalClassification.VERY_STRONG_SETUP,
        }:
            failures.append("setup no longer meets pending quality requirements")
        if signal.data_quality != "VALID":
            failures.append("market data quality became degraded")
        failures.extend(signal.rejection_reasons)
        if signal.invalidation is None:
            failures.append("structural invalidation level is no longer valid")
        if not signal.targets:
            failures.append("target structure is no longer valid")
        if (
            signal.risk_reward is None
            or signal.risk_reward.best_r < self.settings.minimum_acceptable_r
        ):
            failures.append("projected risk/reward fell below minimum")
        return list(dict.fromkeys(failures))

    @staticmethod
    def _inside_entry(record: SignalRecord, price: float) -> bool:
        return (
            record.entry_low is not None
            and record.entry_high is not None
            and record.entry_low <= price <= record.entry_high
        )

    @staticmethod
    def _bullish(record: SignalRecord) -> bool:
        return SignalDirection(record.direction).bullish

    def _stop_crossed(self, record: SignalRecord, price: float) -> bool:
        if record.invalidation_price is None:
            return False
        return (
            price <= record.invalidation_price
            if self._bullish(record)
            else price >= record.invalidation_price
        )

    @staticmethod
    def _next_target(record: SignalRecord) -> SignalTargetRecord | None:
        reached = {x.event_type for x in record.monitoring_events}
        return next((x for x in record.targets if f"TP{x.ordinal}_REACHED" not in reached), None)

    def _weakening_cooldown_ready(self, record: SignalRecord, at: datetime) -> bool:
        relevant = [
            event
            for event in record.monitoring_events
            if event.event_type
            in {
                MonitoringEventType.SIGNAL_WEAKENING.value,
                MonitoringEventType.SIGNAL_RECOVERED.value,
            }
        ]
        if not relevant:
            return True
        latest = max(_aware(event.exchange_timestamp) for event in relevant)
        return (_aware(at) - latest).total_seconds() >= self.settings.weakening_cooldown_seconds

    @staticmethod
    def _ignored(record: SignalRecord, previous: str, reason: str) -> MonitorResult:
        return MonitorResult(
            signal_id=record.id,
            previous_status=previous,
            current_status=record.status,
            ignored_reason=reason,
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
