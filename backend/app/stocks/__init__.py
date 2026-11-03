"""Intraday stocks: NIFTY 50 + NIFTY Next 50 (docs/01 §24).

- `loader.py`: today's index members from NSE, then 5-minute candles from Groww per stock
  (plus NIFTY), in 30-day windows, newest first, resumable.
- `store.py`: reading and writing stock_candles / stock_fetches / stock_members.

PAPER and backtest only. Nothing here places, modifies or cancels an order.
"""
