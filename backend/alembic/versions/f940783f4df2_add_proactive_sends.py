"""add proactive_sends

Revision ID: f940783f4df2
Revises: 5f510ca52271
Create Date: 2026-09-24 05:23:46.948314

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f940783f4df2'
down_revision: Union[str, Sequence[str], None] = '5f510ca52271'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proactive_sends",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("delivered", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Every read is "this user's recent attempts", for the cooldown and the
    # daily count.
    op.create_index("idx_proactive_user_sent_at", "proactive_sends", ["user_id", "sent_at"])


def downgrade() -> None:
    op.drop_index("idx_proactive_user_sent_at", table_name="proactive_sends")
    op.drop_table("proactive_sends")
