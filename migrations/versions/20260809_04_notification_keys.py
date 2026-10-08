"""Support durable lifecycle and new-signal Telegram delivery keys."""

import sqlalchemy as sa
from alembic import op

revision = "20260809_04"
down_revision = "20260809_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_notification_deliveries", sa.Column("delivery_key", sa.String(120), nullable=True)
    )
    op.execute("UPDATE user_notification_deliveries SET delivery_key = 'event:' || event_id")
    with op.batch_alter_table("user_notification_deliveries") as batch:
        batch.alter_column("delivery_key", nullable=False)
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=True)
        batch.create_unique_constraint("uq_user_notification_key", ["user_id", "delivery_key"])


def downgrade() -> None:
    with op.batch_alter_table("user_notification_deliveries") as batch:
        batch.drop_constraint("uq_user_notification_key", type_="unique")
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("delivery_key")
