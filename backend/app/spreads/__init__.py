"""SP-1.0: hedged option spreads on NIFTY weekly options (backtest only, no orders).

Asked by RK on 2026-11-05 (docs/01 §26): after every single-leg strategy failed on costs, test the
three spread ideas the coordinator recommended. Rules fixed on 2026-11-05 BEFORE any result was
seen. Everything not listed here (expiry choice and roll on expiry day = no 0-DTE trades, fills
at the option's 1-minute open, slippage 0.5% + 0.5 point a side on EVERY leg, the full cost stack
per leg with ₹20 brokerage per order, missing data never filled with zero) is the 9/21 EMA
engine's (app/ema921/engine.py). NIFTY only: lot 65, strikes every 50.

A. CS-1.0, credit spread in the ET-1.0 trend direction (rows "CS-100", "CS-150")
   1. Entries and exits are exactly ET-1.0's (app/ema921/trend.py): 5-minute 9/21 EMA cross,
      09:30-14:00, only with the 15-minute trend and the day-open side; every opposite cross
      exits; square-off 15:00.
   2. Bullish signal = bull put spread: SELL the ATM put, BUY the put W points lower.
      Bearish = bear call spread: SELL the ATM call, BUY the call W points higher. W = 100 or 150.
   3. Both legs enter and exit together. If either leg has no price at entry, the signal is
      skipped (NO_DATA).

B. IC-1.0, intraday iron condor on range days (row "IC range days")
   1. A range day, known at 10:00: all 45 one-minute NIFTY candles 09:15-09:59 exist, and their
      high-low range as a % of the 09:15 open is BELOW the median of the same number over the
      previous 20 trading days (fewer than 20 = no trade).
   2. Calm VIX: India VIX's previous close exists, VJ-1.0 does not flag a jump (> 7.8% over 5
      days), and that close is at or below the average of its last 20 closes. Missing = no trade.
   3. Strikes from NIFTY's 09:59 close rounded to 50 (ATM): SELL CE ATM+150 and PE ATM-150,
      BUY CE ATM+350 and PE ATM-350 (200-point wings). All four at the 10:00 open; any leg without
      a price = no trade that day.
   4. Stop per side: when a SOLD leg's 1-minute high reaches 1.5 x its entry price, that leg is
      bought back at the stop (or at the candle's open if it gapped above), and its wing on the
      same side is sold at the open of the next minute. The other side carries on.
   5. Everything still open closes at the 15:15 open.
   Comparison row (not judged): the same condor EVERY day, no range or VIX filter.

C. DS-1.0, debit spread on a strong breakout (rows "DS-100 VF", "DS-150 VF")
   1. Signal: ORB-1.0's breakout (app/orb), taken only when the breakout bar's NIFTY futures
      RVOL is >= 1.5 (VF-1.0, app/volume). No RVOL = no trade.
   2. Bullish = bull call spread: BUY the ATM call, SELL the call W points higher.
      Bearish = bear put spread: BUY the ATM put, SELL the put W points lower. W = 100 or 150.
   3. Exits are ORB-1.0's: a 5-minute close back beyond the far side of the range, else 15:00.
   4. Judged only if at least 80% of the 5-minute bars have an RVOL (as VF-1.0).
   Comparison rows (not judged): the same spreads without the volume filter.

Pass bar (the 5 judged rows, 2 development years, default slippage), same as docs/01 §22 plus a
trade floor: at least 50 trades, net > 0, profit factor >= 1.10, each of the two years net > 0,
max drawdown < 50% of ₹1L. One trade = one whole spread (all its legs, all costs).
Weekly expiry days: every row is also shown WITHOUT trades taken on an expiry day. That split is
information only; the verdict uses all days.
Only the single best passing row (highest profit factor) gets ONE run on the held-back year
(Nov 2023 - Oct 2024): net > 0 and PF >= 1.05. Nothing is re-tuned on the same data.
Margin is not modelled (a hedged NIFTY spread or condor needs well under ₹1L).

Modules: `engine.py` (legs, pairing, condor day filter, scoring), `loader.py` (fetch the legs).
"""

VERSION = "SP-1.0"
UNDERLYING = "NIFTY"
WIDTHS = (100, 150)  # CS-1.0 and DS-1.0 wing distance, points
IC_SHORT_OFFSET = 150
IC_WING = 200
IC_STOP_MULT = 1.5
RANGE_MINUTES = 45  # 09:15-09:59
RANGE_LOOKBACK = 20
VIX_AVG_DAYS = 20
DS_RVOL = 1.5
MIN_TRADES = 50
