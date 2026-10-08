"""Add user arbitrage preferences and two-leg paper positions."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_06"
down_revision = "20260809_05"
branch_labels = None
depends_on = None
MONEY = sa.Numeric(28, 10)


def upgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        for name in (
            "arbitrage_notifications_enabled",
            "cross_exchange_spot_enabled",
            "cross_exchange_futures_enabled",
            "basis_arbitrage_enabled",
            "funding_arbitrage_enabled",
        ):
            batch.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.true())
            )
        batch.add_column(
            sa.Column(
                "minimum_arbitrage_confidence",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="80",
            )
        )
        batch.add_column(
            sa.Column(
                "minimum_arbitrage_net_edge",
                sa.Numeric(10, 6),
                nullable=False,
                server_default="0.05",
            )
        )
        batch.add_column(
            sa.Column(
                "maximum_paper_arbitrage_allocation",
                sa.Numeric(5, 2),
                nullable=False,
                server_default="10",
            )
        )
    op.create_table(
        "paper_arbitrage_positions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("paper_accounts.id"), nullable=False),
        sa.Column(
            "opportunity_id",
            sa.String(36),
            sa.ForeignKey("arbitrage_opportunities.id"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("arbitrage_type", sa.String(40), nullable=False),
        sa.Column("notional", MONEY, nullable=False),
        sa.Column("quantity", MONEY, nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("funding_pnl", MONEY, nullable=False),
        sa.Column("realized_pnl", MONEY, nullable=False),
        sa.Column("exit_reason", sa.Text()),
        sa.UniqueConstraint(
            "account_id", "opportunity_id", name="uq_paper_arbitrage_account_opportunity"
        ),
    )
    for name, columns in (
        ("ix_paper_arbitrage_positions_account_id", ["account_id"]),
        ("ix_paper_arbitrage_positions_opportunity_id", ["opportunity_id"]),
        ("ix_paper_arbitrage_positions_status", ["status"]),
    ):
        op.create_index(name, "paper_arbitrage_positions", columns)
    op.create_table(
        "paper_arbitrage_legs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("paper_arbitrage_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("entry_price", MONEY, nullable=False),
        sa.Column("exit_price", MONEY),
        sa.Column("quantity", MONEY, nullable=False),
    )
    op.create_index("ix_paper_arbitrage_legs_position_id", "paper_arbitrage_legs", ["position_id"])
    op.create_table(
        "paper_arbitrage_fills",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "position_id",
            sa.Integer(),
            sa.ForeignKey("paper_arbitrage_positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("processing_key", sa.String(120), nullable=False),
        sa.Column("fill_type", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gross_pnl", MONEY, nullable=False),
        sa.Column("fees", MONEY, nullable=False),
        sa.Column("funding_pnl", MONEY, nullable=False),
        sa.Column("net_pnl", MONEY, nullable=False),
        sa.UniqueConstraint("position_id", "processing_key", name="uq_paper_arbitrage_fill_key"),
    )
    op.create_index(
        "ix_paper_arbitrage_fills_position_id", "paper_arbitrage_fills", ["position_id"]
    )


def downgrade() -> None:
    op.drop_table("paper_arbitrage_fills")
    op.drop_table("paper_arbitrage_legs")
    op.drop_table("paper_arbitrage_positions")
    with op.batch_alter_table("user_preferences") as batch:
        for name in (
            "maximum_paper_arbitrage_allocation",
            "minimum_arbitrage_net_edge",
            "minimum_arbitrage_confidence",
            "funding_arbitrage_enabled",
            "basis_arbitrage_enabled",
            "cross_exchange_futures_enabled",
            "cross_exchange_spot_enabled",
            "arbitrage_notifications_enabled",
        ):
            batch.drop_column(name)
