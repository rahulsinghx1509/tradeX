"""Summary numbers for a list of trades (all ₹ values are NET of slippage and charges
unless the name says gross)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from app.ema921.engine import Trade


@dataclass
class Summary:
    trades: int
    wins: int
    win_rate: float | None  # % of trades with net PnL > 0 (zero counts as a loss, #17)
    gross_pnl: float
    slippage: float
    charges: float
    net_pnl: float
    profit_factor: float | None  # sum of net wins / |sum of net losses|
    payoff_ratio: float | None  # average net win / |average net loss|
    expectancy: float | None  # average net PnL per trade
    max_drawdown: float  # ₹, peak to trough of cumulative net PnL
    max_drawdown_pct: float | None  # % of (capital + cumulative net PnL) at the peak (#18)
    worst_trade: float | None
    worst_mae: float | None  # worst open loss seen inside any trade
    whipsaws: int
    stale_exits: int

    def as_dict(self) -> dict:
        return asdict(self)


def summarize(trades: list[Trade], capital: float) -> Summary:
    ordered = sorted(trades, key=lambda t: t.entry_time)
    nets = [t.net_pnl for t in ordered]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    n = len(nets)
    equity, peak, max_dd, max_dd_pct = capital, capital, 0.0, 0.0
    for x in nets:
        equity += x
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100)
    loss_sum = -sum(losses)
    maes = [t.mae for t in ordered if t.mae is not None]
    return Summary(
        trades=n,
        wins=len(wins),
        win_rate=round(len(wins) / n * 100, 1) if n else None,
        gross_pnl=round(sum(t.gross_pnl for t in ordered), 2),
        slippage=round(sum(t.slippage for t in ordered), 2),
        charges=round(sum(t.charges.get("total", 0.0) for t in ordered), 2),
        net_pnl=round(sum(nets), 2),
        profit_factor=round(sum(wins) / loss_sum, 2) if loss_sum > 0 else None,
        payoff_ratio=(round((sum(wins) / len(wins)) / (loss_sum / len(losses)), 2)
                      if wins and losses and loss_sum > 0 else None),
        expectancy=round(sum(nets) / n, 2) if n else None,
        max_drawdown=round(max_dd, 2),
        max_drawdown_pct=round(max_dd_pct, 2) if n else None,
        worst_trade=min(nets) if nets else None,
        worst_mae=min(maes) if maes else None,
        whipsaws=sum(1 for t in ordered if t.whipsaw),
        stale_exits=sum(1 for t in ordered if t.stale_exit),
    )
