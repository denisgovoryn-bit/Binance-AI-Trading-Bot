"""Add Telegram users and Decimal paper portfolio persistence."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_03"
down_revision = "20260809_02"
branch_labels = None
depends_on = None
MONEY = sa.Numeric(28, 10)


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(64)),
        sa.Column("first_name", sa.String(128)),
        sa.Column("language_code", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("onboarding_completed", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_telegram_users_telegram_user_id", "telegram_users", ["telegram_user_id"])
    op.create_table(
        "user_preferences",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("telegram_users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("base_currency", sa.String(12), nullable=False),
        sa.Column("enabled_exchanges", sa.JSON(), nullable=False),
        sa.Column("enabled_markets", sa.JSON(), nullable=False),
        sa.Column("minimum_confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("selected_symbols", sa.JSON(), nullable=False),
        sa.Column("selected_timeframes", sa.JSON(), nullable=False),
        sa.Column("receive_signal_notifications", sa.Boolean(), nullable=False),
        sa.Column("receive_monitoring_updates", sa.Boolean(), nullable=False),
        sa.Column("receive_daily_reports", sa.Boolean(), nullable=False),
        sa.Column("receive_weekly_reports", sa.Boolean(), nullable=False),
        sa.Column("receive_monthly_reports", sa.Boolean(), nullable=False),
        sa.Column("paper_profile", sa.String(20), nullable=False),
        sa.Column("paper_trade_mode", sa.String(20), nullable=False),
    )
    op.create_table(
        "paper_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column("base_currency", sa.String(12), nullable=False),
        sa.Column("starting_balance", MONEY, nullable=False),
        sa.Column("current_cash", MONEY, nullable=False),
        sa.Column("current_equity", MONEY, nullable=False),
        sa.Column("realized_pnl", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("simulated_slippage", MONEY, nullable=False),
        sa.Column("peak_equity", MONEY, nullable=False),
        sa.Column("current_drawdown", MONEY, nullable=False),
        sa.Column("max_drawdown", MONEY, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_paper_accounts_user_id", "paper_accounts", ["user_id"])
    op.create_index("ix_paper_accounts_active", "paper_accounts", ["active"])
    op.create_table(
        "paper_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("setup_type", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_entry_price", MONEY, nullable=False),
        sa.Column("simulated_fill_price", MONEY, nullable=False),
        sa.Column("initial_stop", MONEY, nullable=False),
        sa.Column("current_stop", MONEY, nullable=False),
        sa.Column("targets_json", sa.JSON(), nullable=False),
        sa.Column("simulated_notional", MONEY, nullable=False),
        sa.Column("initial_quantity", MONEY, nullable=False),
        sa.Column("remaining_quantity", MONEY, nullable=False),
        sa.Column("closed_quantity", MONEY, nullable=False),
        sa.Column("realized_pnl", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("simulated_slippage", MONEY, nullable=False),
        sa.Column("sizing_method", sa.Text(), nullable=False),
        sa.Column("leverage", sa.Numeric(8, 3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("account_id", "signal_id", name="uq_paper_account_signal"),
    )
    for name, columns in (
        ("ix_paper_positions_account_id", ["account_id"]),
        ("ix_paper_positions_signal_id", ["signal_id"]),
        ("ix_paper_positions_symbol", ["symbol"]),
        ("ix_paper_positions_status", ["status"]),
    ):
        op.create_index(name, "paper_positions", columns)
    op.create_table(
        "paper_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("paper_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("signal_monitoring_events.id"), nullable=False
        ),
        sa.Column("fill_type", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reference_price", MONEY, nullable=False),
        sa.Column("fill_price", MONEY, nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("gross_pnl", MONEY, nullable=False),
        sa.Column("fee", MONEY, nullable=False),
        sa.Column("slippage", MONEY, nullable=False),
        sa.Column("net_pnl", MONEY, nullable=False),
        sa.Column("allocation_pct", sa.Numeric(7, 4), nullable=False),
        sa.UniqueConstraint("position_id", "event_id", name="uq_paper_fill_event"),
    )
    op.create_index("ix_paper_fills_position_id", "paper_fills", ["position_id"])
    op.create_index("ix_paper_fills_event_id", "paper_fills", ["event_id"])
    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cash", MONEY, nullable=False),
        sa.Column("equity", MONEY, nullable=False),
        sa.Column("realized_pnl", MONEY, nullable=False),
        sa.Column("unrealized_pnl", MONEY, nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("drawdown", MONEY, nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
    )
    op.create_index("ix_portfolio_snapshots_account_id", "portfolio_snapshots", ["account_id"])
    op.create_index("ix_portfolio_snapshots_captured_at", "portfolio_snapshots", ["captured_at"])
    op.create_table(
        "user_notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("telegram_users.id"), nullable=False),
        sa.Column(
            "event_id", sa.Integer(), sa.ForeignKey("signal_monitoring_events.id"), nullable=False
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("telegram_message_id", sa.BigInteger()),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("user_id", "event_id", name="uq_user_notification_event"),
    )
    op.create_table(
        "performance_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id")),
        sa.Column("report_type", sa.String(20), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("global_strategy", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "performance_reports",
        "user_notification_deliveries",
        "portfolio_snapshots",
        "paper_fills",
        "paper_positions",
        "paper_accounts",
        "user_preferences",
        "telegram_users",
    ):
        op.drop_table(table)
