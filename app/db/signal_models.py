"""Persistent pre-entry signal records."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class SignalRecord(Base):
    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_fingerprint_status", "fingerprint", "status"),
        Index("ix_signals_current", "status", "current_confidence", "last_evaluated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    correlation_group_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    setup_type: Mapped[str] = mapped_column(String(30), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    analysis_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiry_reason: Mapped[str | None] = mapped_column(Text)
    initial_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    current_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    maximum_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    minimum_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    entry_low: Mapped[float | None] = mapped_column(Float)
    entry_high: Mapped[float | None] = mapped_column(Float)
    ideal_entry: Mapped[float | None] = mapped_column(Float)
    entry_basis: Mapped[str | None] = mapped_column(String(40))
    entry_reason: Mapped[str | None] = mapped_column(Text)
    invalidation_price: Mapped[float | None] = mapped_column(Float)
    original_invalidation_price: Mapped[float | None] = mapped_column(Float)
    entry_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_price_reference: Mapped[float | None] = mapped_column(Float)
    entry_confirmation_type: Mapped[str | None] = mapped_column(String(30))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_monitored_price: Mapped[float | None] = mapped_column(Float)
    current_bid_price: Mapped[float | None] = mapped_column(Float)
    current_ask_price: Mapped[float | None] = mapped_column(Float)
    current_executable_price: Mapped[float | None] = mapped_column(Float)
    last_market_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence_at_entry: Mapped[float | None] = mapped_column(Float)
    confidence_delta_at_entry: Mapped[float | None] = mapped_column(Float)
    revalidation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revalidation_reason: Mapped[str | None] = mapped_column(Text)
    entry_zone_reached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_confidence_after_entry: Mapped[float | None] = mapped_column(Float)
    min_confidence_after_entry: Mapped[float | None] = mapped_column(Float)
    monitoring_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    weakening_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    lifecycle_events: Mapped[list["SignalLifecycleEventRecord"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalLifecycleEventRecord.at",
    )
    updates: Mapped[list["SignalUpdateRecord"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", order_by="SignalUpdateRecord.at"
    )
    targets: Mapped[list["SignalTargetRecord"]] = relationship(
        back_populates="signal", cascade="all, delete-orphan", order_by="SignalTargetRecord.ordinal"
    )
    monitoring_events: Mapped[list["SignalMonitoringEventRecord"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalMonitoringEventRecord.exchange_timestamp",
    )


class SignalLifecycleEventRecord(Base):
    __tablename__ = "signal_lifecycle_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal: Mapped[SignalRecord] = relationship(back_populates="lifecycle_events")


class SignalUpdateRecord(Base):
    __tablename__ = "signal_updates"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    previous_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    current_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    score_components: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal: Mapped[SignalRecord] = relationship(back_populates="updates")


class SignalTargetRecord(Base):
    __tablename__ = "signal_targets"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    r_multiple: Mapped[float] = mapped_column(Float, nullable=False)
    signal: Mapped[SignalRecord] = relationship(back_populates="targets")


class SignalMonitoringEventRecord(Base):
    __tablename__ = "signal_monitoring_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_signal_monitoring_event_key"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"), index=True)
    event_key: Mapped[str] = mapped_column(String(96), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str | None] = mapped_column(String(30))
    previous_confidence: Mapped[float | None] = mapped_column(Float)
    current_confidence: Mapped[float | None] = mapped_column(Float)
    price: Mapped[float | None] = mapped_column(Float)
    configured_price: Mapped[float | None] = mapped_column(Float)
    slippage: Mapped[float | None] = mapped_column(Float)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    analysis_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exchange_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_age_ms: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    signal: Mapped[SignalRecord] = relationship(back_populates="monitoring_events")
