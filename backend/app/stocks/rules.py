"""IS-1.0: intraday stock setups on NIFTY 50 + Next 50, fixed 2026-11-03 BEFORE any result.

Nothing here may be tuned after the backtest has been seen (docs/01 §24). A failed setup is
dropped, not re-tuned on the same data.

Data: 5-minute candles (stock_candles), closed bars only. A signal appears when its bar has
closed; the trade enters at the NEXT bar's open, plus slippage. Missing volume => no VWAP or
RVOL => no signal for that stock that day (missing never becomes zero).

Shared readings (per stock, per day):
- VWAP: cumulative (high+low+close)/3 x volume / cumulative volume, from 09:15.
- EMA 9 and EMA 21 of 5-minute closes, ATR 14 (Wilder) of 5-minute bars, both carried over days.
- Previous close: last close of the previous trading day. Gap = 09:15 open / previous close - 1.
- RVOL: volume of the first bar(s) divided by the average of the same bars over the previous
  20 trading days (at least 10 days needed, otherwise no signal).
- Market filter: longs only while NIFTY's last closed 5-minute bar is above NIFTY's 09:15 open;
  shorts only while below.
- Relative strength (RS): stock % change from previous close minus NIFTY's, at the signal bar.
  Longs need RS > 0, shorts RS < 0.

Setups (each has a LONG row and a SHORT row; one trade per stock per day per setup). The signal
is the FIRST bar in the window that meets every condition (setup + market filter + RS); if that
trade can't be taken (stop distance out of range, no next bar) the stock has no trade that day:
- ORB  (opening-range breakout): range = high/low of 09:15-09:29. First 5-minute close beyond
  the range in bars closing 09:35-11:00, on the side of VWAP it broke to, with RVOL(first 15
  minutes) >= 1.5. Stop = the other side of the range.
- VWP  (VWAP trend pullback): bars closing 10:00-14:00. Long: day change >= +1.0%, RS >= +0.5%,
  EMA9 > EMA21, previous bar's low <= EMA21 (the pullback), signal bar green and closing above
  EMA9 and VWAP. Stop = lowest low of the last 3 bars. Short is the mirror image.
- GAP  (gap and go): 1% <= |gap| <= 4% in the gap's direction, RVOL(09:15 bar) >= 2.0. First
  close beyond the 09:15 bar's high (gap up) / low (gap down), in bars closing 09:25-10:00, on
  the right side of VWAP. Stop = the other end of the 09:15 bar.

Every trade:
- Stop distance must be 0.25%-1.5% of the entry price, else the signal is skipped.
- Target = 2 x the stop distance (2R). If a bar touches both stop and target, the stop counts.
- No entry after 14:30. Anything still open is squared off at the 15:15 bar's open.
- Size: risk ₹1,000 (1% of ₹1L): quantity = ₹1,000 / stop distance, capped so the position is
  at most ₹2.5L (MIS leverage 5x on half the capital; Groww's per-stock leverage NOT_VERIFIED).
- Portfolio per row: at most 2 positions open at once and 5 trades a day; when several signals
  share a bar the highest RVOL goes first; no new entries after the day's closed loss reaches
  ₹3,000.
- Costs per side: slippage 0.05% of price; Groww intraday brokerage = lower of ₹20 and 0.1% of
  the order (NOT_VERIFIED vs Groww's current price list); STT 0.025% on the sell; NSE
  transaction 0.00297%; SEBI 0.0001%; stamp 0.003% on the buy; GST 18% on brokerage +
  transaction + SEBI.

Pass bar (development period, each of the 6 rows): >= 100 trades, net > 0, profit factor >=
1.10, net > 0 in each half of the development period, max drawdown < 25% of ₹1L.
Held-back period = the OLDEST third of the trading days loaded; development = the rest. Only
the single best passing row (highest profit factor) gets one run on the held-back period:
net > 0 and profit factor >= 1.05. Survivorship bias: today's index members only.
"""

from __future__ import annotations

from datetime import time

VERSION = "IS-1.0"
SETUPS = ("ORB", "VWP", "GAP")
DIRECTIONS = {"LONG": 1, "SHORT": -1}

CAPITAL = 100_000.0
RISK_PER_TRADE = 1_000.0
MAX_POSITION_VALUE = 250_000.0
MAX_OPEN = 2
MAX_TRADES_PER_DAY = 5
DAILY_LOSS_STOP = 3_000.0

MIN_STOP_PCT, MAX_STOP_PCT = 0.25, 1.5
TARGET_R = 2.0
LAST_ENTRY = time(14, 30)
SQUARE_OFF = time(15, 15)  # bar START time whose open closes everything

RVOL_DAYS, RVOL_MIN_DAYS = 20, 10
EMA_FAST, EMA_SLOW, ATR_N = 9, 21, 14

ORB_RANGE_BARS = 3          # 09:15, 09:20, 09:25
ORB_SIGNAL = (time(9, 30), time(10, 55))   # signal bar START times (close 09:35-11:00)
ORB_RVOL_MIN = 1.5

VWP_SIGNAL = (time(9, 55), time(13, 55))   # close 10:00-14:00
VWP_DAY_CHANGE_PCT, VWP_RS_PCT = 1.0, 0.5
VWP_STOP_BARS = 3

GAP_MIN_PCT, GAP_MAX_PCT = 1.0, 4.0
GAP_RVOL_MIN = 2.0
GAP_SIGNAL = (time(9, 20), time(9, 55))    # close 09:25-10:00

SLIPPAGE_PCT = 0.05
BROKERAGE_FLAT, BROKERAGE_PCT = 20.0, 0.1
STT_SELL_PCT = 0.025
TXN_PCT = 0.00297
SEBI_PCT = 0.0001
STAMP_BUY_PCT = 0.003
GST_PCT = 18.0

PASS_MIN_TRADES = 100
PASS_PF = 1.10
PASS_MAX_DD_PCT = 25.0
HOLDOUT_PF = 1.05
HOLDOUT_FRACTION = 1 / 3
