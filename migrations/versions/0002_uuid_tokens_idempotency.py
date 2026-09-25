"""uuid public ids, card tokens, idempotency records, query indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-24 16:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "authorization",
        sa.Column("public_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute('UPDATE "authorization" SET public_id = gen_random_uuid()')
    op.alter_column(
        "authorization", "public_id", existing_type=postgresql.UUID(), nullable=False
    )
    op.create_index("ix_authorization_public_id", "authorization", ["public_id"], unique=True)

    op.add_column("card", sa.Column("token", sa.String(length=64), nullable=True))
    op.execute(
        "UPDATE card SET token = 'card_' || substr(md5(random()::text || id::text), 1, 24)"
    )
    op.alter_column("card", "token", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_card_token", "card", ["token"], unique=True)

    op.create_table(
        "idempotency_record",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'completed'"), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_idempotency_record_key"),
    )

    op.create_index("ix_ledger_entry_created_at", "ledger_entry", ["created_at"], unique=False)
    op.create_index(
        "ix_authorization_card_id_created_at",
        "authorization",
        ["card_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_authorization_card_id_created_at", table_name="authorization")
    op.drop_index("ix_ledger_entry_created_at", table_name="ledger_entry")
    op.drop_table("idempotency_record")
    op.drop_index("ix_card_token", table_name="card")
    op.drop_column("card", "token")
    op.drop_index("ix_authorization_public_id", table_name="authorization")
    op.drop_column("authorization", "public_id")
