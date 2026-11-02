"""ORB-1.0: the 15-minute opening-range breakout on index options (backtest only, no orders).

Rules, fixed on 2026-11-02 before any result was seen (docs/01 §22). Run on NIFTY (weekly
expiries, lot 65, strikes every 50) and BANKNIFTY (monthly expiries only, lot 30, strikes every
100). Everything not listed here (fills, slippage, costs, expiry choice and roll, missing-data
handling) is exactly the 9/21 EMA engine's (app/ema921/engine.py).

1. Opening range: the highest high and lowest low of the index's 1-minute candles from 09:15 to
   09:29 (15 candles). A day with any missing spot minute is a no-trade day (same as 9/21 EMA).
2. Signal: the first 5-minute bar, closing from 09:35 to 13:00, whose close is above the range
   high (long) or below the range low (short). A close equal to the high or low is not a break.
   At most one trade a day: after that day's first break nothing else is entered.
3. Entry: the option's open in the minute the signal bar closes (first candle within 2 minutes).
   Modes as 9/21 EMA: deep ITM buy (5 strikes in), ATM buy, ATM sell (sell the put on a long).
4. Stop: a later 5-minute bar closing back beyond the other side of the range (long: close below
   the range low; short: above the range high) exits at the option's open of the next minute
   (RANGE_STOP).
5. Otherwise square off at 15:00.
6. Size and costs: 1 lot, slippage 0.5% + 0.5 point a side, full cost stack (NOT_VERIFIED rates).
7. Pass bar on the 2 development years at default slippage, per underlying and mode: net > 0,
   profit factor >= 1.10, each year net > 0, max drawdown < 50% of ₹1L. Only the single best
   passing mode across ORB-1.0 and ET-1.0 gets one run on the held-back year (Nov 2023 - Oct
   2024): net > 0 and profit factor >= 1.05. Nothing is re-tuned on the same data.

Modules: `engine.py` (signals -> trades), `loader.py` (fetch the option legs it needs).
"""
