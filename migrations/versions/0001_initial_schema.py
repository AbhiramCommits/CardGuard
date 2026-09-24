"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-24 15:15:56.525756

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('company',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('account',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('company_id', sa.BigInteger(), nullable=False),
    sa.Column('account_type', sa.Enum('available_credit', 'holds', 'settled', name='account_type'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'account_type', name='uq_account_company_type')
    )
    op.create_index(op.f('ix_account_company_id'), 'account', ['company_id'], unique=False)
    op.create_table('employee',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('company_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_index(op.f('ix_employee_company_id'), 'employee', ['company_id'], unique=False)
    op.create_table('card',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('last_four', sa.String(length=4), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_card_employee_id'), 'card', ['employee_id'], unique=False)
    op.create_table('spend_policy',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('monthly_limit_cents', sa.BigInteger(), nullable=False),
    sa.Column('per_transaction_limit_cents', sa.BigInteger(), nullable=False),
    sa.Column('blocked_mccs', postgresql.ARRAY(sa.String(length=4)), nullable=False),
    sa.Column('velocity_max_auths', sa.Integer(), nullable=False),
    sa.Column('velocity_window_minutes', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['employee.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('employee_id')
    )
    op.create_table('authorization',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('card_id', sa.BigInteger(), nullable=False),
    sa.Column('merchant_name', sa.String(length=255), nullable=False),
    sa.Column('mcc', sa.String(length=4), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.Enum('approved', 'declined', 'pending_review', 'expired', 'captured', 'reversed', name='authorization_status'), nullable=False),
    sa.Column('decision_reason', sa.String(length=255), nullable=True),
    sa.Column('risk_score', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('amount_cents > 0', name='ck_authorization_amount_positive'),
    sa.ForeignKeyConstraint(['card_id'], ['card.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_authorization_card_id'), 'authorization', ['card_id'], unique=False)
    op.create_table('ledger_posting',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('authorization_id', sa.BigInteger(), nullable=False),
    sa.Column('entry_type', sa.Enum('hold', 'capture', 'reversal', 'hold_release', name='entry_type'), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['authorization_id'], ['authorization.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('idempotency_key')
    )
    op.create_index(op.f('ix_ledger_posting_authorization_id'), 'ledger_posting', ['authorization_id'], unique=False)
    op.create_table('ledger_entry',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('authorization_id', sa.BigInteger(), nullable=False),
    sa.Column('account_id', sa.BigInteger(), nullable=False),
    sa.Column('direction', sa.Enum('debit', 'credit', name='direction'), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('entry_type', sa.Enum('hold', 'capture', 'reversal', 'hold_release', name='entry_type'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('posting_id', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('amount_cents > 0', name='ck_ledger_entry_amount_positive'),
    sa.ForeignKeyConstraint(['account_id'], ['account.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['authorization_id'], ['authorization.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['posting_id'], ['ledger_posting.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('authorization_id', 'entry_type', 'account_id', 'direction', name='uq_ledger_entry_posting_group')
    )
    op.create_index(op.f('ix_ledger_entry_account_id'), 'ledger_entry', ['account_id'], unique=False)
    op.create_index(op.f('ix_ledger_entry_authorization_id'), 'ledger_entry', ['authorization_id'], unique=False)
    op.create_index(op.f('ix_ledger_entry_posting_id'), 'ledger_entry', ['posting_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entry_posting_id'), table_name='ledger_entry')
    op.drop_index(op.f('ix_ledger_entry_authorization_id'), table_name='ledger_entry')
    op.drop_index(op.f('ix_ledger_entry_account_id'), table_name='ledger_entry')
    op.drop_table('ledger_entry')
    op.drop_index(op.f('ix_ledger_posting_authorization_id'), table_name='ledger_posting')
    op.drop_table('ledger_posting')
    op.drop_index(op.f('ix_authorization_card_id'), table_name='authorization')
    op.drop_table('authorization')
    op.drop_table('spend_policy')
    op.drop_index(op.f('ix_card_employee_id'), table_name='card')
    op.drop_table('card')
    op.drop_index(op.f('ix_employee_company_id'), table_name='employee')
    op.drop_table('employee')
    op.drop_index(op.f('ix_account_company_id'), table_name='account')
    op.drop_table('account')
    op.drop_table('company')

    bind = op.get_bind()
    for type_name in ("account_type", "authorization_status", "entry_type", "direction"):
        sa.Enum(name=type_name).drop(bind, checkfirst=False)
