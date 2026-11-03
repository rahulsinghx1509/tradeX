"""Multi-strategy PAPER signal engine: three strategies run live on NIFTY options, minute by
minute, and every trade gets a number ("Trade #12") in its entry and exit Telegram messages.

Strategies (each exactly its backtest rules, re-run every minute with as_of = now):
- EMA-921: 9/21 EMA cross on 5-minute bars, 09:30-14:30, 2 x ATR trailing stop (app/ema921)
- ORB-1.0: 15-minute opening-range breakout, one trade a day (app/orb)
- ET-1.0:  9/21 EMA cross taken only with the 15-minute trend (app/ema921/trend.py)

One execution mode for all (default ATM buy, 1 lot), so each signal is one trade to act on.
PAPER ONLY (RK, 2026-11-03): no strategy has passed a backtest yet. Nothing here places,
modifies or cancels an order.
"""
