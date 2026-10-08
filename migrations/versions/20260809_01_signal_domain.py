"""Create the frozen Phase 3B signal persistence schema."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("correlation_group_key", sa.String(160), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("setup_type", sa.String(30), nullable=False),
        sa.Column("timeframe", sa.String(8), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("expiry_reason", sa.Text()),
        sa.Column("initial_confidence", sa.Float(), nullable=False),
        sa.Column("current_confidence", sa.Float(), nullable=False),
        sa.Column("maximum_confidence", sa.Float(), nullable=False),
        sa.Column("minimum_confidence", sa.Float(), nullable=False),
        sa.Column("entry_low", sa.Float()),
        sa.Column("entry_high", sa.Float()),
        sa.Column("ideal_entry", sa.Float()),
        sa.Column("invalidation_price", sa.Float()),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    for name, columns in (
        ("ix_signals_fingerprint", ["fingerprint"]),
        ("ix_signals_correlation_group_key", ["correlation_group_key"]),
        ("ix_signals_symbol", ["symbol"]),
        ("ix_signals_status", ["status"]),
        ("ix_signals_fingerprint_status", ["fingerprint", "status"]),
        ("ix_signals_current", ["status", "current_confidence", "last_evaluated_at"]),
    ):
        op.create_index(name, "signals", columns)
    op.create_table(
        "signal_lifecycle_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.id", ondelete="CASCADE")),
        sa.Column("previous_status", sa.String(30)),
        sa.Column("new_status", sa.String(30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_signal_lifecycle_events_signal_id", "signal_lifecycle_events", ["signal_id"]
    )
    op.create_table(
        "signal_updates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.id", ondelete="CASCADE")),
        sa.Column("previous_confidence", sa.Float(), nullable=False),
        sa.Column("current_confidence", sa.Float(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_signal_updates_signal_id", "signal_updates", ["signal_id"])
    op.create_table(
        "signal_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("signal_id", sa.String(36), sa.ForeignKey("signals.id", ondelete="CASCADE")),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(10), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("r_multiple", sa.Float(), nullable=False),
    )
    op.create_index("ix_signal_targets_signal_id", "signal_targets", ["signal_id"])


def downgrade() -> None:
    for table in ("signal_targets", "signal_updates", "signal_lifecycle_events", "signals"):
        op.drop_table(table)
