"""Multi-strategy PAPER signal trades (numbered)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03 06:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

PRICE = sa.Numeric(precision=14, scale=4)
MONEY = sa.Numeric(precision=14, scale=2)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table('signal_trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('strategy', sa.String(length=16), nullable=False),
    sa.Column('trade_date', sa.Date(), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('signal_time', TS, nullable=False),
    sa.Column('direction', sa.Integer(), nullable=False),
    sa.Column('side', sa.String(length=4), nullable=False),
    sa.Column('option_type', sa.String(length=2), nullable=False),
    sa.Column('strike', PRICE, nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=False),
    sa.Column('groww_symbol', sa.String(length=64), nullable=False),
    sa.Column('lot_size', sa.Integer(), nullable=False),
    sa.Column('entry_time', TS, nullable=False),
    sa.Column('entry_price', PRICE, nullable=False),
    sa.Column('entry_fill', PRICE, nullable=False),
    sa.Column('nifty_entry', PRICE, nullable=True),
    sa.Column('exit_plan', sa.String(length=300), nullable=False),
    sa.Column('exit_time', TS, nullable=True),
    sa.Column('exit_price', PRICE, nullable=True),
    sa.Column('exit_fill', PRICE, nullable=True),
    sa.Column('exit_reason', sa.String(length=20), nullable=True),
    sa.Column('nifty_exit', PRICE, nullable=True),
    sa.Column('stale_exit', sa.Boolean(), nullable=False),
    sa.Column('gross_pnl', MONEY, nullable=True),
    sa.Column('slippage', MONEY, nullable=True),
    sa.Column('charges', MONEY, nullable=True),
    sa.Column('net_pnl', MONEY, nullable=True),
    sa.Column('status', sa.String(length=6), nullable=False),
    sa.Column('created_at', TS, nullable=False),
    sa.Column('closed_at', TS, nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('strategy', 'trade_date', 'signal_time', name='uq_signal_trade')
    )
    op.create_index(op.f('ix_signal_trades_trade_date'), 'signal_trades', ['trade_date'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_signal_trades_trade_date'), table_name='signal_trades')
    op.drop_table('signal_trades')
