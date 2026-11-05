"""TP-1.0: NIFTY trend-pullback option buying (backtest only, no orders).

Asked by RK on 2026-11-05 (docs/01 §26): test the plan a ChatGPT answer suggested for a ₹1 lakh
account. Its words were turned into exact rules on 2026-11-05 BEFORE any result was seen.
Everything not listed (expiry and roll, fills at the option's 1-minute open, slippage 0.5% +
0.5 point a side, full cost stack, missing data never zero) is the 9/21 EMA engine's.

Chart: NIFTY spot 5-minute bars, EMA 9 and 21 running across days (as EMA-921).
VWAP: the NIFTY index has no volume, so VWAP is NIFTY near-month FUTURES VWAP from 09:15
(typical price (H+L+C)/3 x volume, VF-1.0's `fut-load` series), compared with the FUTURES close
(spot vs futures VWAP would mix in the futures premium). A minute without a volume makes VWAP
unknown for the rest of that day = no entries.
Opening range (OR): high / low of the 1-minute spot candles 09:15-09:29 (as ORB-1.0).

Long (call) setup, bar by bar; the short (put) setup is the exact mirror image:
1. Breakout: a 5-minute bar closing at or after 09:35 above the OR high arms the long side for the
   rest of the day.
2. Pullback: after that bar, a bar whose low touches EMA 9 (low <= EMA 9) or whose futures low
   touches VWAP. A bar closing below EMA 21 cancels a pullback in progress.
3. Confirmation: a LATER bar that closes green (close > open), above EMA 9, with EMA 9 > EMA 21
   and the futures close above VWAP. Not a chase: its high-low must be <= 2 x ATR(14) of the 5m
   bars, else the pullback stays open and the next bar can confirm.
4. Entry: the option's open in the minute the confirmation bar closes. Confirmation bars must
   close 10:00-12:00 or 13:30-14:45 (no trades in the 12:00-13:30 lull or after 14:45).
5. Stop (index): the lowest low from the pullback bar to the confirmation bar. R = entry close -
   stop (R <= 0 = no trade). Target: entry + 1.5 R.
6. Exits, on 1-minute NIFTY closes after entry, at the option's open of the next minute:
   STOP (close <= stop), TARGET (close >= target), STALL (30 minutes after entry the best close
   so far never reached entry + 0.5 R). INVALID: a 5-minute close below EMA 21. Square-off 15:15.
7. One trade open at a time, at most 2 trades a day. After a trade, a NEW pullback is needed.
8. Weekly expiry days (a NIFTY expiry date) are not traded at all.
9. Modes: ATM buy, and 1-strike ITM buy (50 points in the money). 1 lot of 65.

Risk limits (the ChatGPT plan, rows "... limits"), checked before each entry:
- Estimated 1-lot risk = R x delta x 65, delta 0.5 (ATM) / 0.6 (1 ITM): a rough rule of thumb,
  NOT_VERIFIED against real Greeks. Above ₹500 = skip (RISK_TOO_BIG).
- Day net loss reached ₹1,000 = no more entries that day; week (Mon-Sun) net loss ₹2,500 = none
  that week. Losses count realised NET P&L (after costs) of closed trades.

Pass bar (the 2 rows WITH limits; rows without limits are shown for comparison): 2 development
years, default slippage, at least 50 trades, net > 0, profit factor >= 1.10, each year net > 0,
max drawdown < 50% of ₹1L. Only the single best passing row of SP-1.0 and TP-1.0 together gets
ONE held-back-year run (Nov 2023 - Oct 2024): net > 0 and PF >= 1.05. Not judged if fewer than
80% of the 5-minute bars have a futures VWAP.
"""

VERSION = "TP-1.0"
UNDERLYING = "NIFTY"
MODES = ("ATM_BUY", "ITM1_BUY")
MODE_LABELS = {"ATM_BUY": "ATM buy", "ITM1_BUY": "1-ITM buy"}
DELTA = {"ATM_BUY": 0.5, "ITM1_BUY": 0.6}
RISK_PER_TRADE = 500.0
DAY_LOSS = 1000.0
WEEK_LOSS = 2500.0
MAX_TRADES_DAY = 2
TARGET_R = 1.5
STALL_MINUTES = 30
STALL_R = 0.5
CHASE_ATR = 2.0
