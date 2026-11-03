"""Intraday stocks: member list, 5-minute candles, fetch log

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-03 08:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

PRICE = sa.Numeric(precision=14, scale=4)
TS = sa.DateTime(timezone=True)
BIGPK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')


def upgrade() -> None:
    op.create_table('stock_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('index_name', sa.String(length=20), nullable=False),
    sa.Column('groww_symbol', sa.String(length=64), nullable=False),
    sa.Column('updated_at', TS, nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', name='uq_stock_member')
    )
    op.create_table('stock_candles',
    sa.Column('id', BIGPK, nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('candle_time', TS, nullable=False),
    sa.Column('open', PRICE, nullable=True),
    sa.Column('high', PRICE, nullable=True),
    sa.Column('low', PRICE, nullable=True),
    sa.Column('close', PRICE, nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'candle_time', name='uq_stock_candle')
    )
    op.create_index('ix_stock_candles_symbol_date', 'stock_candles', ['symbol', 'trade_date'],
                    unique=False)
    op.create_table('stock_fetches',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('window_start', sa.Date(), nullable=False),
    sa.Column('window_end', sa.Date(), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('fetched_at', TS, nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('symbol', 'window_start', name='uq_stock_fetch')
    )


def downgrade() -> None:
    op.drop_table('stock_fetches')
    op.drop_index('ix_stock_candles_symbol_date', table_name='stock_candles')
    op.drop_table('stock_candles')
    op.drop_table('stock_members')
