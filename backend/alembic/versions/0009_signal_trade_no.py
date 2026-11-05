"""Date-based PAPER trade numbers: signal_trades.trade_no = YYYYMMDD-NN (RK 2026-11-05)

Past trades are numbered the same way: per trade_date, in the order they were recorded (id).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-05 06:00:00
"""
from datetime import date

import sqlalchemy as sa

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('signal_trades') as b:
        b.add_column(sa.Column('trade_no', sa.String(length=16), nullable=True))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, trade_date FROM signal_trades ORDER BY trade_date, id")).fetchall()
    count: dict = {}
    for rid, day in rows:
        if isinstance(day, str):  # SQLite hands dates back as text
            day = date.fromisoformat(day[:10])
        count[day] = count.get(day, 0) + 1
        conn.execute(sa.text("UPDATE signal_trades SET trade_no = :no WHERE id = :id"),
                     {"no": f"{day:%Y%m%d}-{count[day]:02d}", "id": rid})
    with op.batch_alter_table('signal_trades') as b:
        b.create_unique_constraint('uq_signal_trade_no', ['trade_no'])


def downgrade() -> None:
    with op.batch_alter_table('signal_trades') as b:
        b.drop_constraint('uq_signal_trade_no', type_='unique')
        b.drop_column('trade_no')
