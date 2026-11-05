"""VF-1.0: NIFTY futures volume, shown in the live alerts and tested as an entry filter.

Asked by RK on 2026-11-03 ("can we add volume in our analysis live", chose "Both"). Rules fixed
on 2026-11-03 BEFORE any result was seen (docs/01 §25):

1. Volume source: the NIFTY index has no volume, so NIFTY NEAR-MONTH FUTURES 1-minute volume is
   used. Near month = the monthly futures contract with the earliest expiry on or after the day
   (on expiry day it is still the expiring contract). Stored as one continuous series under
   FUT_SYMBOL. Contract list from Groww get_expiries / get_contracts (expired futures served:
   confirmed for DAILY candles back to 2020; 1-minute history is NOT_VERIFIED until `fut-load`
   runs on the server).
2. Bar volume: the sum of the 5 one-minute volumes of a 5-minute bar (aligned to 09:15). A bar
   with a missing minute or a missing volume has NO volume (missing stays missing, never 0).
3. Relative volume (RVOL) of a bar = its volume / the average volume of the SAME 5-minute slot
   (same time of day) over the previous LOOKBACK_DAYS days that have a volume for that slot.
   Fewer than LOOKBACK_DAYS such days = no RVOL. Same-slot averaging is used because intraday
   volume is U-shaped (the 09:15-09:30 bars are always the busiest); comparing them with the
   last 20 bars of the same day would call every opening bar "high volume".
4. Filter: an entry is taken only if the RVOL of its SIGNAL bar (the 5-minute bar whose close
   triggered it) is >= the threshold. No RVOL = no entry (NO TRADE). Exits are never filtered.
   ORB-1.0 trades only the day's first breakout, so a filtered first break means no ORB trade
   that day (later breaks are not taken).
5. Thresholds tested: 1.0 ("above its usual volume") and 1.5 ("clearly above"). Nothing else is
   tuned. Strategies: EMA-921, ORB-1.0 and ET-1.0 on NIFTY, all 3 modes = 18 filtered rows,
   next to the 9 unfiltered rows for comparison.
6. Pass bar, same as ORB/ET (docs/01 §22), on the 2 development years at default slippage:
   net > 0, profit factor >= 1.10, each year net > 0, max drawdown < 50% of ₹1L. Only the best
   passing row gets ONE run on the held-back year (Nov 2023 - Oct 2024): net > 0 and PF >= 1.05.
   18 rows means one PASS can be luck; that is why the held-back check exists.
7. Live: every PAPER entry alert shows the signal bar's RVOL as information. The live filter is
   switched on only if a row passes and RK approves. PAPER only, never orders.
"""

VERSION = "VF-1.0"
FUT_SYMBOL = "NSE-NIFTY-FUT-NEAR"  # continuous near-month NIFTY futures 1m series (rule 1)
UNDERLYING = "NIFTY"
TIMEFRAME = 5
LOOKBACK_DAYS = 20
THRESHOLDS = (1.0, 1.5)
