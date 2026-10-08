"""Persistent arbitrage opportunity lifecycle."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class ArbitrageOpportunityRecord(Base):
    __tablename__ = "arbitrage_opportunities"
    __table_args__ = (Index("ix_arbitrage_fingerprint_status", "fingerprint", "status"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    arbitrage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    symbol: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    buy_exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    buy_market: Mapped[str] = mapped_column(String(20), nullable=False)
    sell_exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    sell_market: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_updates: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    gross_spread_pct: Mapped[Any] = mapped_column(Numeric(24, 12), nullable=False)
    net_edge_pct: Mapped[Any] = mapped_column(Numeric(24, 12), nullable=False)
    expected_net_pnl: Mapped[Any] = mapped_column(Numeric(24, 12), nullable=False)
    target_notional: Mapped[Any] = mapped_column(Numeric(24, 8), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    events: Mapped[list["ArbitrageLifecycleEventRecord"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )
    updates: Mapped[list["ArbitrageUpdateRecord"]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )


class ArbitrageLifecycleEventRecord(Base):
    __tablename__ = "arbitrage_lifecycle_events"
    __table_args__ = (UniqueConstraint("event_key", name="uq_arbitrage_event_key"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("arbitrage_opportunities.id", ondelete="CASCADE"), index=True
    )
    event_key: Mapped[str] = mapped_column(String(96), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opportunity: Mapped[ArbitrageOpportunityRecord] = relationship(back_populates="events")


class ArbitrageUpdateRecord(Base):
    __tablename__ = "arbitrage_updates"
    id: Mapped[int] = mapped_column(primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("arbitrage_opportunities.id", ondelete="CASCADE"), index=True
    )
    previous_net_edge_pct: Mapped[Any] = mapped_column(Numeric(24, 12), nullable=False)
    current_net_edge_pct: Mapped[Any] = mapped_column(Numeric(24, 12), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    opportunity: Mapped[ArbitrageOpportunityRecord] = relationship(back_populates="updates")


class PaperArbitragePositionRecord(Base):
    __tablename__ = "paper_arbitrage_positions"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "opportunity_id", name="uq_paper_arbitrage_account_opportunity"
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("arbitrage_opportunities.id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    arbitrage_type: Mapped[str] = mapped_column(String(40), nullable=False)
    notional: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    quantity: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    fees: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    funding_pnl: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    realized_pnl: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    exit_reason: Mapped[str | None] = mapped_column(Text)
    legs: Mapped[list["PaperArbitrageLegRecord"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )
    fills: Mapped[list["PaperArbitrageFillRecord"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class PaperArbitrageLegRecord(Base):
    __tablename__ = "paper_arbitrage_legs"
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("paper_arbitrage_positions.id", ondelete="CASCADE"), index=True
    )
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    exchange: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    entry_price: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    exit_price: Mapped[Any | None] = mapped_column(Numeric(28, 10))
    quantity: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    position: Mapped[PaperArbitragePositionRecord] = relationship(back_populates="legs")


class PaperArbitrageFillRecord(Base):
    __tablename__ = "paper_arbitrage_fills"
    __table_args__ = (
        UniqueConstraint("position_id", "processing_key", name="uq_paper_arbitrage_fill_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("paper_arbitrage_positions.id", ondelete="CASCADE"), index=True
    )
    processing_key: Mapped[str] = mapped_column(String(120), nullable=False)
    fill_type: Mapped[str] = mapped_column(String(20), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gross_pnl: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    fees: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    funding_pnl: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    net_pnl: Mapped[Any] = mapped_column(Numeric(28, 10), nullable=False)
    position: Mapped[PaperArbitragePositionRecord] = relationship(back_populates="fills")
