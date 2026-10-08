"""Immutable audit records for directional scan decisions."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class DirectionalScanCycleRecord(Base):
    __tablename__ = "directional_scan_cycles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), index=True)
    expected_markets: Mapped[int] = mapped_column(nullable=False)
    evaluated_markets: Mapped[int] = mapped_column(nullable=False, default=0)
    failed_markets: Mapped[int] = mapped_column(nullable=False, default=0)
    expected_by_market: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    errors: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False, default=list)
    duration_ms: Mapped[float | None] = mapped_column(Float)


class DirectionalEvaluationRecord(Base):
    __tablename__ = "directional_evaluations"
    __table_args__ = (
        Index("ix_directional_evaluations_window", "evaluated_at", "exchange", "market"),
        Index("ix_directional_evaluations_result", "classification", "selected"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    cycle_id: Mapped[str] = mapped_column(
        ForeignKey("directional_scan_cycles.id", ondelete="CASCADE"), index=True
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    analysis_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    final_state: Mapped[str] = mapped_column(String(30), nullable=False)
    persistence_action: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    scorable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    primary_rejection_reason: Mapped[str | None] = mapped_column(Text)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    score_components: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
