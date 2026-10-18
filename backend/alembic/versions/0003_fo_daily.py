"""NSE daily F&O file rows

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18 16:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('fo_daily',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('instrument', sa.String(length=3), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('strike', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('open', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('high', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('low', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('close', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('settle', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('underlying', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('open_interest', sa.BigInteger(), nullable=True),
    sa.Column('oi_change', sa.BigInteger(), nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('lot_size', sa.Integer(), nullable=True),
    sa.Column('file_format', sa.String(length=6), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_fo_daily_symbol_date', 'fo_daily', ['symbol', 'trade_date'], unique=False)
    op.create_index(op.f('ix_fo_daily_trade_date'), 'fo_daily', ['trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fo_daily_trade_date'), table_name='fo_daily')
    op.drop_index('ix_fo_daily_symbol_date', table_name='fo_daily')
    op.drop_table('fo_daily')
