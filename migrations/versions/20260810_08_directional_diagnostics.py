"""Add immutable directional scanner diagnostic audits."""

import sqlalchemy as sa
from alembic import op

revision = "20260810_08"
down_revision = "20260810_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "directional_scan_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expected_markets", sa.Integer(), nullable=False),
        sa.Column("evaluated_markets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_markets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_by_market", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("duration_ms", sa.Float()),
    )
    op.create_index(
        "ix_directional_scan_cycles_started_at", "directional_scan_cycles", ["started_at"]
    )
    op.create_index("ix_directional_scan_cycles_status", "directional_scan_cycles", ["status"])
    op.create_table(
        "directional_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "cycle_id",
            sa.String(36),
            sa.ForeignKey("directional_scan_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("market", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(40), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("classification", sa.String(30), nullable=False),
        sa.Column("final_state", sa.String(30), nullable=False),
        sa.Column("persistence_action", sa.String(30), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("scorable", sa.Boolean(), nullable=False),
        sa.Column("primary_rejection_reason", sa.Text()),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    for name, columns in (
        ("ix_directional_evaluations_cycle_id", ["cycle_id"]),
        ("ix_directional_evaluations_evaluated_at", ["evaluated_at"]),
        ("ix_directional_evaluations_symbol", ["symbol"]),
        ("ix_directional_evaluations_window", ["evaluated_at", "exchange", "market"]),
        ("ix_directional_evaluations_result", ["classification", "selected"]),
    ):
        op.create_index(name, "directional_evaluations", columns)


def downgrade() -> None:
    op.drop_table("directional_evaluations")
    op.drop_table("directional_scan_cycles")
