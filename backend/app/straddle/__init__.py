"""ST-1.0: the 09:20 hedged short straddle on NIFTY weekly options (backtest only, no orders).

Rules, fixed on 2026-11-01 before any result was seen (docs/01 §21):

1. Days: NSE trading days with NIFTY 1-minute data. Skip a day when India VIX jumped (VJ-1.0:
   the previous close is more than 7.8% above the close 5 trading days earlier) or when that
   VIX close is missing (stale data = NO TRADE).
2. Expiry: the nearest weekly NIFTY expiry after today. On expiry day itself, roll to the next
   one (no 0-DTE trades), the same as the 9/21 EMA rules.
3. Strikes: NIFTY's 09:19 one-minute close, rounded to the nearest 50 = ATM.
   Sell 1 lot ATM CE + 1 lot ATM PE. Buy 1 lot CE at ATM + 500 and 1 lot PE at ATM - 500 as
   protection (the "wings").
4. Entry: all four legs at the open of their 09:20 one-minute candle (first candle within 2
   minutes). If any leg has no price, the day is skipped (NO_DATA), never filled with zero.
5. Stop: each SOLD leg has its own stop at 30% above its entry price. The first 1-minute candle
   whose high reaches the stop closes that leg: at the stop price, or at the candle's open if it
   opened above the stop (a gap). The other legs carry on. No re-entry.
6. Exit: everything still open is closed at the open of the 15:15 one-minute candle.
7. Size and costs: 65 units per leg. Slippage 0.5% + 0.5 point per side, against us, the same
   as the EMA backtest, plus brokerage, STT, exchange, SEBI, stamp and GST (rates NOT_VERIFIED).
8. Pass bar to spend the held-back year (Nov 2023 - Oct 2024), at the default slippage on the
   2 development years: net PnL > 0, profit factor >= 1.10, and each of the two years net > 0.
   The held-back check then passes if net > 0 and profit factor >= 1.05. Nothing is re-tuned.
"""
