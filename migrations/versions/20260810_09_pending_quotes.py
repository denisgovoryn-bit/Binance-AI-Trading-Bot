"""Persist executable quote diagnostics for pending entries."""

import sqlalchemy as sa
from alembic import op

revision = "20260810_09"
down_revision = "20260810_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("current_bid_price", sa.Float()))
    op.add_column("signals", sa.Column("current_ask_price", sa.Float()))
    op.add_column("signals", sa.Column("current_executable_price", sa.Float()))


def downgrade() -> None:
    op.drop_column("signals", "current_executable_price")
    op.drop_column("signals", "current_ask_price")
    op.drop_column("signals", "current_bid_price")
