"""1-minute candles, fetch log and option contracts for the 9/21 EMA backtest

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 12:30:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

BIG_PK = sa.BigInteger().with_variant(sa.Integer(), 'sqlite')
PRICE = sa.Numeric(precision=14, scale=4)


def upgrade() -> None:
    op.create_table('minute_candles',
    sa.Column('id', BIG_PK, nullable=False),
    sa.Column('groww_symbol', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('candle_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', PRICE, nullable=True),
    sa.Column('high', PRICE, nullable=True),
    sa.Column('low', PRICE, nullable=True),
    sa.Column('close', PRICE, nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('open_interest', sa.BigInteger(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('groww_symbol', 'candle_time', name='uq_minute_candle')
    )
    op.create_index('ix_minute_candle_symbol_day', 'minute_candles', ['groww_symbol', 'trade_date'],
                    unique=False)
    op.create_table('minute_fetches',
    sa.Column('id', BIG_PK, nullable=False),
    sa.Column('groww_symbol', sa.String(length=64), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('groww_symbol', 'trade_date', name='uq_minute_fetch')
    )
    op.create_table('option_contracts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('underlying', sa.String(length=32), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('strike', PRICE, nullable=True),
    sa.Column('option_type', sa.String(length=2), nullable=True),
    sa.Column('groww_symbol', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('groww_symbol', name='uq_option_contract')
    )
    op.create_index('ix_option_contract_lookup', 'option_contracts', ['underlying', 'expiry_date'],
                    unique=False)


def downgrade() -> None:
    op.drop_index('ix_option_contract_lookup', table_name='option_contracts')
    op.drop_table('option_contracts')
    op.drop_table('minute_fetches')
    op.drop_index('ix_minute_candle_symbol_day', table_name='minute_candles')
    op.drop_table('minute_candles')
