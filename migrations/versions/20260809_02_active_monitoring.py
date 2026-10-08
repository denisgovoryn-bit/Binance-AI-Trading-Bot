"""Add active monitoring state and idempotent event history."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_02"
down_revision = "20260809_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("original_invalidation_price", sa.Float()),
        sa.Column("entry_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("entry_price_reference", sa.Float()),
        sa.Column("entry_confirmation_type", sa.String(30)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("current_monitored_price", sa.Float()),
        sa.Column("last_market_event_at", sa.DateTime(timezone=True)),
        sa.Column("confidence_at_entry", sa.Float()),
        sa.Column("max_confidence_after_entry", sa.Float()),
        sa.Column("min_confidence_after_entry", sa.Float()),
        sa.Column("monitoring_degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("weakening_reasons", sa.JSON(), nullable=False, server_default="[]"),
    )
    for column in columns:
        op.add_column("signals", column)
    op.execute("UPDATE signals SET original_invalidation_price = invalidation_price")
    op.create_table(
        "signal_monitoring_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "signal_id",
            sa.String(36),
            sa.ForeignKey("signals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_key", sa.String(96), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("previous_status", sa.String(30)),
        sa.Column("new_status", sa.String(30)),
        sa.Column("previous_confidence", sa.Float()),
        sa.Column("current_confidence", sa.Float()),
        sa.Column("price", sa.Float()),
        sa.Column("configured_price", sa.Float()),
        sa.Column("slippage", sa.Float()),
        sa.Column("reasons", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True)),
        sa.Column("exchange_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_age_ms", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("event_key", name="uq_signal_monitoring_event_key"),
    )
    op.create_index(
        "ix_signal_monitoring_events_signal_id", "signal_monitoring_events", ["signal_id"]
    )
    op.create_index(
        "ix_signal_monitoring_events_event_type", "signal_monitoring_events", ["event_type"]
    )


def downgrade() -> None:
    op.drop_table("signal_monitoring_events")
    with op.batch_alter_table("signals") as batch:
        for name in (
            "weakening_reasons",
            "monitoring_degraded",
            "min_confidence_after_entry",
            "max_confidence_after_entry",
            "confidence_at_entry",
            "last_market_event_at",
            "current_monitored_price",
            "stopped_at",
            "closed_at",
            "completed_at",
            "entry_confirmation_type",
            "entry_price_reference",
            "entry_confirmed_at",
            "original_invalidation_price",
        ):
            batch.drop_column(name)
