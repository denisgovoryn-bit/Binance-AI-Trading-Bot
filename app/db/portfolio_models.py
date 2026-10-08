"""Persistent users, preferences, paper accounts, positions, fills and reports."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base

MONEY = Numeric(28, 10)


class TelegramUserRecord(Base):
    __tablename__ = "telegram_users"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    language_code: Mapped[str] = mapped_column(String(16), default="en")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    preferences: Mapped["UserPreferencesRecord"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    accounts: Mapped[list["PaperAccountRecord"]] = relationship(back_populates="user")


class UserPreferencesRecord(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[int] = mapped_column(
        ForeignKey("telegram_users.id", ondelete="CASCADE"), primary_key=True
    )
    base_currency: Mapped[str] = mapped_column(String(12), default="USDT")
    enabled_exchanges: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["BINANCE", "BYBIT"])
    enabled_markets: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["SPOT", "FUTURES"])
    minimum_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("80"))
    selected_symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    selected_timeframes: Mapped[list[str]] = mapped_column(JSON, default=list)
    receive_signal_notifications: Mapped[bool] = mapped_column(Boolean, default=True)
    receive_monitoring_updates: Mapped[bool] = mapped_column(Boolean, default=True)
    receive_daily_reports: Mapped[bool] = mapped_column(Boolean, default=False)
    receive_weekly_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    receive_monthly_reports: Mapped[bool] = mapped_column(Boolean, default=True)
    paper_profile: Mapped[str] = mapped_column(String(20), default="BALANCED")
    paper_trade_mode: Mapped[str] = mapped_column(String(20), default="AUTO_PAPER")
    arbitrage_notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cross_exchange_spot_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    cross_exchange_futures_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    basis_arbitrage_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    funding_arbitrage_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    minimum_arbitrage_confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("80")
    )
    minimum_arbitrage_net_edge: Mapped[Decimal] = mapped_column(
        Numeric(10, 6), default=Decimal("0.05")
    )
    maximum_paper_arbitrage_allocation: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), default=Decimal("10")
    )
    user: Mapped[TelegramUserRecord] = relationship(back_populates="preferences")


class PaperAccountRecord(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    base_currency: Mapped[str] = mapped_column(String(12), default="USDT")
    starting_balance: Mapped[Decimal] = mapped_column(MONEY)
    current_cash: Mapped[Decimal] = mapped_column(MONEY)
    current_equity: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    simulated_slippage: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    peak_equity: Mapped[Decimal] = mapped_column(MONEY)
    current_drawdown: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    max_drawdown: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user: Mapped[TelegramUserRecord] = relationship(back_populates="accounts")
    positions: Mapped[list["PaperPositionRecord"]] = relationship(back_populates="account")
    snapshots: Mapped[list["PortfolioSnapshotRecord"]] = relationship(back_populates="account")


class PaperPositionRecord(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (UniqueConstraint("account_id", "signal_id", name="uq_paper_account_signal"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("signals.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(20))
    market: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    direction: Mapped[str] = mapped_column(String(12))
    timeframe: Mapped[str] = mapped_column(String(8))
    setup_type: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reference_entry_price: Mapped[Decimal] = mapped_column(MONEY)
    simulated_fill_price: Mapped[Decimal] = mapped_column(MONEY)
    initial_stop: Mapped[Decimal] = mapped_column(MONEY)
    current_stop: Mapped[Decimal] = mapped_column(MONEY)
    targets_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    simulated_notional: Mapped[Decimal] = mapped_column(MONEY)
    initial_quantity: Mapped[Decimal] = mapped_column(MONEY)
    remaining_quantity: Mapped[Decimal] = mapped_column(MONEY)
    closed_quantity: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    fees: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    simulated_slippage: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0"))
    sizing_method: Mapped[str] = mapped_column(Text)
    leverage: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    status: Mapped[str] = mapped_column(String(20), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    account: Mapped[PaperAccountRecord] = relationship(back_populates="positions")
    fills: Mapped[list["PaperFillRecord"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class PaperFillRecord(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (UniqueConstraint("position_id", "event_id", name="uq_paper_fill_event"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_id: Mapped[int] = mapped_column(
        ForeignKey("paper_positions.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[int] = mapped_column(ForeignKey("signal_monitoring_events.id"), index=True)
    fill_type: Mapped[str] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reference_price: Mapped[Decimal] = mapped_column(MONEY)
    fill_price: Mapped[Decimal] = mapped_column(MONEY)
    quantity: Mapped[Decimal] = mapped_column(MONEY)
    gross_pnl: Mapped[Decimal] = mapped_column(MONEY)
    fee: Mapped[Decimal] = mapped_column(MONEY)
    slippage: Mapped[Decimal] = mapped_column(MONEY)
    net_pnl: Mapped[Decimal] = mapped_column(MONEY)
    allocation_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    position: Mapped[PaperPositionRecord] = relationship(back_populates="fills")


class PortfolioSnapshotRecord(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cash: Mapped[Decimal] = mapped_column(MONEY)
    equity: Mapped[Decimal] = mapped_column(MONEY)
    realized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    unrealized_pnl: Mapped[Decimal] = mapped_column(MONEY)
    fees: Mapped[Decimal] = mapped_column(MONEY)
    drawdown: Mapped[Decimal] = mapped_column(MONEY)
    reason: Mapped[str] = mapped_column(String(40))
    account: Mapped[PaperAccountRecord] = relationship(back_populates="snapshots")


class UserNotificationDeliveryRecord(Base):
    __tablename__ = "user_notification_deliveries"
    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_user_notification_event"),
        UniqueConstraint("user_id", "delivery_key", name="uq_user_notification_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("telegram_users.id"), index=True)
    event_id: Mapped[int | None] = mapped_column(
        ForeignKey("signal_monitoring_events.id"), index=True
    )
    delivery_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None] = mapped_column(Text)


class PerformanceReportRecord(Base):
    __tablename__ = "performance_reports"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    report_type: Mapped[str] = mapped_column(String(20))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    global_strategy: Mapped[bool] = mapped_column(Boolean, default=False)
