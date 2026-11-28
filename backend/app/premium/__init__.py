"""PR-1.0 "Theta Harvest" (TH-1.0, name chosen 2026-11-28): two hedged NIFTY premium-selling
candidates, held OVERNIGHT, on NSE's daily option file.

Asked by RK 2026-11-28 ("consider yourself a top trader ... create a strategy ... profitable in
past data"). docs/01 §29. Rules and pass bar below were written and committed BEFORE any result
was seen. They are not tuned afterwards: if a candidate fails, it is recorded as failed.

Why these two (research, docs/01 §29):
- Every intraday option-selling test here failed (ST-1.0 straddle, IC-1.0 condor, CS credit
  spreads, ET-1.0 retest). Bhat, Pandey & Rao (2024, Journal of Futures Markets 44(8)) found on
  NIFTY options (2017-2020) that SHORT option positions earn positive, highly significant returns
  OVERNIGHT and negative returns INTRADAY. Muravyev & Ni (2020, JFE) found the same split on
  S&P 500 options. So sellers are paid for holding risk while the market is shut, not during the
  day: an intraday seller sits on the wrong side of the clock.
- Jones & Shemesh (2018, Journal of Finance): option returns are significantly lower (good for
  sellers) over non-trading periods, mostly weekends, because prices under-count the market's
  closed days.
- Options on NIFTY are usually overpriced (our OS-1.0: realised vol below ATM IV on 59-74% of
  days), variance risk is priced in India (Sankar et al. 2020, IREF), and after a VIX jump the
  next month runs hotter (our VJ-1.0, the one rule that passed a final test).
- At 1 lot, costs (~₹200-700 a trade) killed every small intraday edge. Both candidates trade at
  most once a week, and B pays exit costs only on legs that finish in the money.

Common to both (fixed):
- NIFTY weekly options only, 1 lot of LOT = 65 (today's lot; history used 75/50/25, a fixed lot
  keeps years comparable).
- Structure: iron fly. Sell the ATM call and ATM put (strike nearest NIFTY's close), buy a call and
  a put WING away. WING = ATM straddle price (call + put close) rounded to the nearest 50, at
  least 100: the wings sit near the break-evens, so the worst loss is known at entry and the
  position is hedged (Groww margin for a hedged sell is about ₹50k a side; iron fly margin is
  NOT_VERIFIED, to be checked only if a candidate passes).
- Prices: the day's closing price from NSE's F&O file (fo_daily); a leg must have traded that day
  at entry. Fills: sell at close - 0.5% - 0.5 pt, buy at close + 0.5% + 0.5 pt, to the tick (the
  ema921 engine default), plus the full charge stack (ema921.costs). 2x slippage = 1% + 1 pt.
- VJ-1.0 (project rule): no option selling when India VIX's 5-day rise, on the trading day BEFORE
  entry, is above 7.8%, or when that VIX value is missing (NO_DATA = NO TRADE).

Candidate A - WT-1.0 "weekend theta" (Jones & Shemesh + overnight premium):
- Entry: the close of every trading day whose next trading day is 3+ calendar days later (Fridays
  and the last day before a long weekend), unless VJ-1.0 blocks it.
- Expiry: the nearest weekly expiry AFTER the exit day (the position never expires while held).
- Exit: the next trading day's close, all four legs (a leg with no trade that day is valued at
  NSE's settle price, and counted).

Candidate B - WV-1.0 "weekly volatility premium" (VRP, held through every night to expiry):
- Entry: the close of the first trading day after each weekly expiry, for the next weekly expiry
  (at least 2 calendar days away), only if
  - VJ-1.0 does not block it, and
  - India VIX on the day before entry is ABOVE NIFTY's 20-day realised volatility to that day
    (close-to-close, annualised): options are priced above what the market has been doing.
- Exit: held to expiry, settled on NIFTY's close on the expiry day (NSE's final settlement).
  No stop: the wings cap the loss. Legs that finish in the money pay STT on exercise (0.125% of
  their value, long legs) and are charged ₹20 brokerage each (assumed, NOT_VERIFIED).

Pass bar, for each candidate separately (RK's bar, coordinator's stress tests, all required):
1. at least MIN_TRADES trades;
2. profit factor (net) >= PF_MIN;
3. max drawdown of closed-trade equity < DD_MAX of the ₹1L account;
4. every calendar year net > 0 (2026 is judged too: it has 11 months);
5. net > 0 at 2x slippage;
6. net > 0 with the best DROP_BEST trades removed.

Out-of-sample: nothing is fitted (every number above comes from theory, the engine default or an
earlier tested rule), so every year is out-of-sample for these rules. Caveat: VS-1.0 and OS-1.0
already looked at 2020-2025 option prices (monthly flies broke even), which shaped the choice of a
weekly, overnight design. Two candidates are tested, not a sweep. Reference rows (no filters) are
shown only to explain, never judged and never swapped in.
"""

from __future__ import annotations

from datetime import date

VERSION = "PR-1.0"
SYMBOL = "NIFTY"
LOT = 65
STRIKE_STEP = 50.0
MIN_WING = 100.0

SLIP_PCT, SLIP_PTS = 0.005, 0.5  # 1x; 2x doubles both
EXERCISE_STT = 0.00125  # on the settlement value of exercised long legs
SETTLE_BROKERAGE = 20.0  # per in-the-money leg settled at expiry (assumption)

VJ_JUMP_PCT = 7.8  # = vix_warning.JUMP_PCT (asserted in tests)
HV_DAYS = 20
WEEKEND_GAP_DAYS = 3  # next trading day this many calendar days later or more
MIN_DTE_B = 2

ACCOUNT = 100_000.0
MIN_TRADES = 100
PF_MIN = 1.10
DD_MAX = 0.20
DROP_BEST = 5

FIRST_DAY = date(2020, 1, 1)

CANDIDATES = {
    "A": "TH-A (WT-1.0) weekend: iron fly Friday close -> next trading day close, VJ-1.0 filter",
    "B": "TH-B (WV-1.0) weekly: iron fly day after expiry -> expiry, VJ-1.0 + VIX > HV20 filter",
}
REFERENCES = {
    "A0": "A without the VJ-1.0 filter (reference only)",
    "B0": "B without any filter (reference only)",
}
