"""NSE participant-wise open interest

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19 11:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('participant_oi',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('client_type', sa.String(length=10), nullable=False),
    sa.Column('fut_idx_long', sa.BigInteger(), nullable=True),
    sa.Column('fut_idx_short', sa.BigInteger(), nullable=True),
    sa.Column('fut_stk_long', sa.BigInteger(), nullable=True),
    sa.Column('fut_stk_short', sa.BigInteger(), nullable=True),
    sa.Column('opt_idx_call_long', sa.BigInteger(), nullable=True),
    sa.Column('opt_idx_put_long', sa.BigInteger(), nullable=True),
    sa.Column('opt_idx_call_short', sa.BigInteger(), nullable=True),
    sa.Column('opt_idx_put_short', sa.BigInteger(), nullable=True),
    sa.Column('opt_stk_call_long', sa.BigInteger(), nullable=True),
    sa.Column('opt_stk_put_long', sa.BigInteger(), nullable=True),
    sa.Column('opt_stk_call_short', sa.BigInteger(), nullable=True),
    sa.Column('opt_stk_put_short', sa.BigInteger(), nullable=True),
    sa.Column('total_long', sa.BigInteger(), nullable=True),
    sa.Column('total_short', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('trade_date', 'client_type', name='uq_participant_oi')
    )
    op.create_index(op.f('ix_participant_oi_trade_date'), 'participant_oi', ['trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_participant_oi_trade_date'), table_name='participant_oi')
    op.drop_table('participant_oi')
