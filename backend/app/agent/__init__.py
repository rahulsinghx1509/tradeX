"""AD-1.0 "adaptive agent": picks and re-tunes NIFTY option strategies each month by itself,
from a fixed menu of building blocks, and proves it on months it has not seen (backtest + PAPER
only, never orders).

Asked by RK on 2026-11-28 ("make an agent who will see the data and then decide which strategy
would work according to the scene", "strategy will also be modified accordingly by an agent",
"not only these 4, it can use its own strategy", "intraday or weekly or weekend, try all").
docs/01 §30. Everything below was written and committed BEFORE any result was seen. If the
walk-forward fails, it is recorded as failed and not tuned afterwards.

What "agent" means here (project rule: no LLM decides trades)
- A fixed, repeatable search. Each month it scores every candidate strategy on the last
  TRAIN_MONTHS months of trades AFTER COSTS, keeps the few that pass its own checks, and trades
  only those in the next month, which it has not seen. Then it does the same again.
- A candidate = one choice from each list of building blocks below. The lists are fixed here; the
  agent cannot add blocks. The number of candidates is capped (MAX_CANDIDATES) because the more
  combinations are tried, the more of them look good by luck.

Market condition ("scene"), known before the day starts (daily data up to the PREVIOUS day)
- TREND: NIFTY daily ADX(14) >= 25.   RANGE: ADX(14) < 20.  (20-25 is neither.)
- HIGHVOL: India VIX close above its own 60-day median.  LOWVOL: otherwise.
- ANY: every day. Missing ADX or VIX = no trade that day (missing data = NO TRADE).
- Live, intraday only (re-checked at each signal minute, RK 2026-11-28): LIVE_TREND = NIFTY's
  last 1-minute close is 0.4%+ away from the day's open; LIVE_FLAT = less than 0.2% away. So the
  agent can trade a range strategy in the morning and a trend strategy after the market moves.
- VJ-1.0 (the project's tested VIX-jump rule): no option SELLING on its warning days.

Intraday building blocks (NIFTY index 1-minute candles grouped into 5-minute bars from 09:15;
first signal of the day only, entry at the option's open in the minute the signal bar closes)
- Entry (7): ORB (first 5-min close outside the 09:15-09:29 range, by 12:30); EMA (9/21 EMA
  cross, 09:45-14:30); GAPFADE / GAPGO (gap 0.3%-1.0% vs the previous close, the 09:15-09:19 bar
  closes back toward / away from the previous close: fade it / go with it at 09:20); RSI (RSI(14)
  crosses back up through 25 / down through 75, 10:00-14:30); BB (close back inside Bollinger
  20,2 after closing outside, 10:00-14:30); TRENDPB (price 0.3%+ beyond the day's open, bar
  touches EMA21 and closes back on the trend side, 10:00-14:00).
  Added 2026-11-28 at RK's request ("add more strategy which is in market"), still before any
  result: SUPERTREND (Supertrend 10,3 on 5-min bars flips, 09:45-14:30); MACD (12,26,9 line
  crosses its signal, 09:45-14:30); PDHL (5-min close crosses the previous day's high / low,
  09:20-14:30); CPR (5-min close crosses above the top / below the bottom of the central pivot
  range from the previous day's H/L/C, 09:20-14:30); IB30 (first 5-min close outside the
  09:15-09:44 range, 09:50-13:30).
- Stop (2): 0.25% or 0.5% of NIFTY at entry.
- Exit style (3): 2R (target 2 x the stop); NONE (stop or time only); TRAIL (adaptive: once
  NIFTY is 1 x the stop in profit the stop moves to the entry price, then trails 1 x the stop
  distance behind the best price; RK 2026-11-28 "change rules according to market in live hours").
- Time exit (2): 60 minutes after entry, or 15:15. Stops/targets are checked on NIFTY 1-minute
  highs/lows; the option is closed at the next minute's open (stop first if both in one minute).
- Structure (4), strikes from NIFTY at entry rounded to 50, nearest weekly expiry (next one on
  expiry day): BUY_ATM; BUY_ITM (one strike in the money); DEBIT (buy ATM, sell 2 strikes out of
  the money, same type); CREDIT (sell the ATM option of the OTHER type, buy 4 strikes further
  out: a hedged sell, about ₹50k margin).
Swing building blocks (NSE daily option closes, fo_daily, 2020-now)
- Holding (3): OVERNIGHT (close -> next trading day's close, normal weekdays), WEEKEND (close
  before a 3+ day gap -> next trading day's close), WEEKLY (day after an expiry -> held to the
  next expiry, cash-settled on NIFTY's close).
- Condition, from the previous day's data: neutral ALWAYS / VRP (VIX above NIFTY's 20-day realised
  volatility) / RANGE (ADX < 20); directional TREND (close vs EMA20 with ADX >= 20 gives the side)
  / REVERT (RSI(2) < 10 long, > 90 short).
- Structure: neutral FLY (iron fly, wings = straddle price, min 100) / CONDOR (sell 100 points
  out, buy 300 points out); directional CREDIT (sell ATM, buy 200 points further out) / DEBIT
  (buy ATM, sell 200 points out) / LONG (buy ATM).
Every candidate also takes one market-condition filter: intraday 7 (ANY, TREND, RANGE, HIGHVOL,
LOWVOL, LIVE_TREND, LIVE_FLAT), swing 5 (no live ones).
Count: intraday 12 x 2 x 3 x 2 x 4 x 7 = 4,032; swing 3 x 12 x 5 = 180; total 4,212 (cap).

Fills and costs: 1 lot of 65 for every date (fixed so years compare); option price +/- 0.5% +
0.5 point to the tick (ema921 default), full charge stack (ema921/costs.py, NOT_VERIFIED rates),
2 orders per leg. 2x slippage = 1% + 1 point. Expiry settlement: intrinsic value, 0.125% STT on
exercised long legs and an assumed ₹20 per in-the-money leg (as PR-1.0). A leg with no price is
never guessed: the trade is skipped and counted.

The agent each month (walk-forward)
- Training window: the TRAIN_MONTHS calendar months before the month being traded.
- A candidate is eligible if in that window: >= MIN_TRAIN_TRADES trades, net profit factor >=
  TRAIN_PF, net > 0, and still net > 0 without its TRAIN_DROP best trades (not one lucky trade).
- Score = net mean / standard deviation x sqrt(trades) (rewards steady profit, not one big win).
- Picks: up to PICK_INTRADAY intraday candidates with DIFFERENT entry types and up to PICK_SWING
  swing candidates with DIFFERENT holdings, best score first. Nothing eligible = NO TRADE month.
- Account: ₹1L. At most one intraday position and one swing position open at once, at most
  MAX_INTRADAY_DAY intraday trades a day. Same-minute signals: the better-scored pick wins.
  Day loss DAY_LOSS / week (Mon-Sun) loss WEEK_LOSS (realised) = no new entries for the day/week.
  Kill switch: realised drawdown from the peak >= KILL_DD = no new entries for the rest of the
  month; the agent re-tunes next month.
- The 2x slippage check re-runs the WHOLE walk-forward at 2x (training scores included).

Pass bar (the verdict row is FULL = intraday + swing, months where both kinds of data exist;
every rule above is fixed; the other rows are information only)
1. >= MIN_TRADES trades;  2. net profit factor >= PF_MIN;  3. max drawdown < DD_MAX of ₹1L;
4. every calendar year net > 0;  5. net > 0 at 2x slippage;  6. net > 0 without the best DROP_BEST
trades;  7. more than half of the traded months net > 0 (stricter bar for generated strategies).
PASS means: worth PAPER trading after RK approves. Never real orders.
"""

from __future__ import annotations

from datetime import date, time

VERSION = "AD-1.0"
SYMBOL = "NIFTY"
LOT = 65
STEP = 50.0
INTRADAY_FIRST = date(2023, 11, 1)
SWING_FIRST = date(2020, 1, 1)

# Market condition
ADX_TREND, ADX_RANGE = 25.0, 20.0
VIX_MEDIAN_DAYS = 60
REGIMES = ("ANY", "TREND", "RANGE", "HIGHVOL", "LOWVOL")
LIVE_REGIMES = ("LIVE_TREND", "LIVE_FLAT")  # intraday only, checked at the signal minute
I_REGIMES = REGIMES + LIVE_REGIMES
LIVE_TREND_PCT, LIVE_FLAT_PCT = 0.004, 0.002

# Intraday blocks
ENTRIES = ("ORB", "EMA", "GAPFADE", "GAPGO", "RSI", "BB", "TRENDPB",
           "SUPERTREND", "MACD", "PDHL", "CPR", "IB30")
STOPS = (0.0025, 0.005)
EXITS = ("2R", "NONE", "TRAIL")
TIME_EXITS = (60, None)
I_STRUCTURES = ("BUY_ATM", "BUY_ITM", "DEBIT", "CREDIT")
GAP_MIN, GAP_MAX = 0.003, 0.010
TREND_AWAY = 0.003
RSI_LOW, RSI_HIGH = 25.0, 75.0
ST_ATR, ST_MULT = 10, 3.0
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
SQUARE_OFF = time(15, 15)
PRICE_GRACE = 3  # minutes to wait for an option's first price at entry

# Swing blocks
HOLDINGS = ("OVERNIGHT", "WEEKEND", "WEEKLY")
NEUTRAL = {"ALWAYS": ("FLY", "CONDOR"), "VRP": ("FLY", "CONDOR"), "RANGE": ("FLY", "CONDOR")}
DIRECTIONAL = {"TREND": ("CREDIT", "DEBIT", "LONG"), "REVERT": ("CREDIT", "DEBIT", "LONG")}
SELLING = {"CREDIT", "FLY", "CONDOR"}
WEEKEND_GAP_DAYS = 3
RSI2_LOW, RSI2_HIGH = 10.0, 90.0

# Fills
SLIP_PCT, SLIP_PTS = 0.005, 0.5

# Agent
TRAIN_MONTHS = 6
MIN_TRAIN_TRADES = 10
TRAIN_PF = 1.20
TRAIN_DROP = 2
PICK_INTRADAY = 3
PICK_SWING = 2
MAX_INTRADAY_DAY = 2
DAY_LOSS = 3000.0
WEEK_LOSS = 6000.0
KILL_DD = 15000.0
MAX_CANDIDATES = 4212

# Pass bar
ACCOUNT = 100_000.0
MIN_TRADES = 60
PF_MIN = 1.10
DD_MAX = 0.20
DROP_BEST = 5
