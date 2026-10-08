"""Add persistent arbitrage opportunities and lifecycle history."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_05"
down_revision = "20260809_04"
branch_labels = None
depends_on = None
DECIMAL = sa.Numeric(24, 12)


def upgrade() -> None:
    op.create_table(
        "arbitrage_opportunities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("arbitrage_type", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("buy_exchange", sa.String(20), nullable=False),
        sa.Column("buy_market", sa.String(20), nullable=False),
        sa.Column("sell_exchange", sa.String(20), nullable=False),
        sa.Column("sell_market", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_updates", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("gross_spread_pct", DECIMAL, nullable=False),
        sa.Column("net_edge_pct", DECIMAL, nullable=False),
        sa.Column("expected_net_pnl", DECIMAL, nullable=False),
        sa.Column("target_notional", sa.Numeric(24, 8), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    for name, columns in (
        ("ix_arbitrage_opportunities_fingerprint", ["fingerprint"]),
        ("ix_arbitrage_opportunities_symbol", ["symbol"]),
        ("ix_arbitrage_opportunities_status", ["status"]),
        ("ix_arbitrage_fingerprint_status", ["fingerprint", "status"]),
    ):
        op.create_index(name, "arbitrage_opportunities", columns)
    op.create_table(
        "arbitrage_lifecycle_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.String(36),
            sa.ForeignKey("arbitrage_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(96), nullable=False, unique=True),
        sa.Column("previous_status", sa.String(20)),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_arbitrage_lifecycle_events_opportunity_id",
        "arbitrage_lifecycle_events",
        ["opportunity_id"],
    )
    op.create_table(
        "arbitrage_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "opportunity_id",
            sa.String(36),
            sa.ForeignKey("arbitrage_opportunities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_net_edge_pct", DECIMAL, nullable=False),
        sa.Column("current_net_edge_pct", DECIMAL, nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_arbitrage_updates_opportunity_id", "arbitrage_updates", ["opportunity_id"])


def downgrade() -> None:
    op.drop_table("arbitrage_updates")
    op.drop_table("arbitrage_lifecycle_events")
    op.drop_table("arbitrage_opportunities")
