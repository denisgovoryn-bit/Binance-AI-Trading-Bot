"""Add conditional pending-entry audit fields."""

import sqlalchemy as sa
from alembic import op

revision = "20260810_07"
down_revision = "20260809_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = (
        sa.Column("waiting_since", sa.DateTime(timezone=True)),
        sa.Column("entry_basis", sa.String(40)),
        sa.Column("entry_reason", sa.Text()),
        sa.Column("confidence_delta_at_entry", sa.Float()),
        sa.Column("revalidation_started_at", sa.DateTime(timezone=True)),
        sa.Column("revalidated_at", sa.DateTime(timezone=True)),
        sa.Column("revalidation_reason", sa.Text()),
        sa.Column("entry_zone_reached_at", sa.DateTime(timezone=True)),
    )
    for column in columns:
        op.add_column("signals", column)
    op.execute("UPDATE signals SET waiting_since = created_at WHERE status = 'WAITING_ENTRY'")


def downgrade() -> None:
    with op.batch_alter_table("signals") as batch:
        for name in (
            "entry_zone_reached_at",
            "revalidation_reason",
            "revalidated_at",
            "revalidation_started_at",
            "confidence_delta_at_entry",
            "entry_reason",
            "entry_basis",
            "waiting_since",
        ):
            batch.drop_column(name)
