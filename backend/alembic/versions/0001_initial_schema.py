"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-16 07:07:39.716139
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('instruments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=32), nullable=False),
    sa.Column('instrument_type', sa.String(length=10), nullable=False),
    sa.Column('exchange', sa.String(length=10), nullable=False),
    sa.Column('segment', sa.String(length=10), nullable=False),
    sa.Column('expiry_date', sa.Date(), nullable=True),
    sa.Column('strike_price', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('option_type', sa.String(length=2), nullable=True),
    sa.Column('lot_size', sa.Integer(), nullable=True),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('provider_symbol', sa.String(length=64), nullable=False),
    sa.Column('trading_symbol', sa.String(length=64), nullable=True),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('provider', 'provider_symbol', name='uq_instrument')
    )
    op.create_table('ingestion_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('job', sa.String(length=20), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=True),
    sa.Column('timeframe', sa.String(length=5), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('rows_received', sa.Integer(), nullable=False),
    sa.Column('rows_written', sa.Integer(), nullable=False),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('market_candles',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('timeframe', sa.String(length=5), nullable=False),
    sa.Column('candle_time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('open', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('high', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('low', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('close', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('open_interest', sa.BigInteger(), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('quality_status', sa.String(length=10), nullable=False),
    sa.Column('quality_reason', sa.String(length=200), nullable=True),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'timeframe', 'candle_time', name='uq_candle')
    )
    op.create_table('market_snapshots',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('instrument_id', sa.Integer(), nullable=False),
    sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_price', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('bid_price', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('ask_price', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('day_open', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('day_high', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('day_low', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('previous_close', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('volume', sa.BigInteger(), nullable=True),
    sa.Column('open_interest', sa.BigInteger(), nullable=True),
    sa.Column('oi_change', sa.BigInteger(), nullable=True),
    sa.Column('implied_volatility', sa.Numeric(precision=10, scale=4), nullable=True),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('is_sample', sa.Boolean(), nullable=False),
    sa.Column('is_stale', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('instrument_id', 'observed_at', name='uq_snapshot')
    )
    op.create_index('ix_snapshot_instrument_time', 'market_snapshots', ['instrument_id', 'observed_at'], unique=False)
    op.create_table('data_quality_log',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('instrument_id', sa.Integer(), nullable=True),
    sa.Column('timeframe', sa.String(length=5), nullable=True),
    sa.Column('check_name', sa.String(length=40), nullable=False),
    sa.Column('status', sa.String(length=5), nullable=False),
    sa.Column('details', sa.JSON(), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instrument_id'], ['instruments.id'], ),
    sa.ForeignKeyConstraint(['run_id'], ['ingestion_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_dq_instrument_time', 'data_quality_log', ['instrument_id', 'checked_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_dq_instrument_time', table_name='data_quality_log')
    op.drop_table('data_quality_log')
    op.drop_index('ix_snapshot_instrument_time', table_name='market_snapshots')
    op.drop_table('market_snapshots')
    op.drop_table('market_candles')
    op.drop_table('ingestion_runs')
    op.drop_table('instruments')
