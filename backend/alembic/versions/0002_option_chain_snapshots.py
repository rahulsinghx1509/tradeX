"""option chain snapshots

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18 12:40:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('option_chain_snapshots',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('taken_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('strikes', sa.Integer(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'symbol', 'expiry_date', 'trade_date', name='uq_chain_day')
    )


def downgrade() -> None:
    op.drop_table('option_chain_snapshots')
