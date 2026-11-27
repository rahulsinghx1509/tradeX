"""MR-4: intraday mean reversion on NIFTY and BANKNIFTY options (backtest only, no orders).

Asked by RK on 2026-11-20 ("lets backtest MR-4 strategy on 3 yr data"), from the rule list R1-R31
the coordinator posted that day (docs/01 §27). The rules were fixed BEFORE any result was seen.
Where the posted words left a choice open, the choice made is written here ("Settled:").

Data and costs
R1. Index spot 1-minute candles from Groww, grouped into 5-minute (Setup A) and 15-minute
    (Setup B) bars aligned to 09:15. Three years: Nov 2023 - Oct 2026, scored per year.
R2. Option fills and costs are the 9/21 EMA engine's: the option's 1-minute open, slippage 0.5% +
    0.5 point a side, the full cost stack (brokerage ₹20 an order, STT, exchange, SEBI, stamp, GST).
    Every option is BOUGHT (R23), so STT is on the sell side only.

Day filter (any true = no trades for that index that day)
R3. NIFTY: any NIFTY expiry day. BANKNIFTY: its MONTHLY expiry day (the last BANKNIFTY expiry of
    the calendar month; before Nov 2024 BANKNIFTY also had weekly expiries, those days trade).
R4. RBI policy decision, Union Budget, or Lok Sabha result day (app/data/mr4_event_days.csv).
    Settled: "election result" = the general election (4 Jun 2024); state results are not listed.
R5. India VIX at 09:15 > 20. VIX at 09:15 = open of the 09:15 one-minute INDIAVIX candle. If Groww
    has no 1-minute VIX for the day, the previous daily VIX close is used and counted in the
    report (VIX_FALLBACK). No VIX at all = no trade.
R6. The 09:15-10:14 range (60 one-minute candles) > 0.8% (BANKNIFTY 1.1%) of the 09:15 open
    blocks Setup B for the day (it is known at 10:15, before the first Setup B entry).
    Also no trade on a day whose spot 1-minute data has any gap (as every earlier backtest).

Direction bias (daily closes up to YESTERDAY, known before the open)
R7. LONG bias: close > SMA(200) and RSI(2) < 30 -> only longs, full size.
R8. SHORT bias: close < SMA(200) and RSI(2) > 70 -> only shorts, full size.
R9. Otherwise both directions at HALF size. RSI = Wilder's. Daily closes from the stored daily
    candles (official close), else the day's last 1-minute close. Fewer than 200 closes = no trade.
    Settled: "half size" of 1 lot is simulated as half the quantity (P&L of 0.5 lot); a real
    account would need 2 lots for full size. Same for the 50/50 exits of R19.

Setup A: gap fill (5-minute chart)
R10. Gap = 09:15 open / previous close - 1. 0.3%-0.8% (BANKNIFTY 0.4%-1.1%). A gap UP is faded
     with a short, a gap DOWN with a long; the bias must allow that direction.
R11. The 09:15-09:20 bar must close back toward the previous close (gap up: close < open; gap
     down: close > open). Entry at 09:20 (option open of the 09:20 minute). No entry after 10:00:
     the 09:20 entry is the only one. Settled: no other entry time.
R12. Target: spot reaches the previous close. Stop: the 09:15-09:29 high (short) / low (long).
     Settled: that level is only complete at 09:30, so the stop is checked from 09:30 on.
     Setup A has no 90-minute exit (R22 is listed under Setup B); it closes at 15:15 at the latest.

Setup B: confluence fade (15-minute chart, 10:00-14:45)
R13. Session VWAP and bands: the index has no volume, so each minute's spot typical price
     (H+L+C)/3 is weighted by that minute's NEAR-MONTH FUTURES volume (VF-1.0 series; BANKNIFTY
     stored under its own name). Bands = VWAP +/- 2 x the volume-weighted standard deviation of
     the typical price around VWAP, both from 09:15 to the bar's last minute. A minute missing
     its price or volume makes VWAP unknown for the rest of the day (no Setup B entries).
R14. Bollinger: SMA(20) of 15-minute closes +/- 2 x their standard deviation (population),
     running across days.
R15. LONG stretch: a bar closes below the lower VWAP band AND its low <= lower Bollinger band.
R16. Reversal: hammer (lower wick >= 2 x body, upper wick <= 0.3 x body, body in the top third
     of the range) or morning star (bar 1 red with body >= 0.6 x its range; bar 2 body <= 0.3 x
     bar 1 body; bar 3 green, closing above bar 1's body midpoint). Settled: the stretch (R15)
     must be on the hammer bar or the bar before it; for a star, on any of its 3 bars.
R17. SHORT: the mirror (shooting star / evening star at the upper bands).
R18. Entry at the next bar's open = the option open in the minute the reversal bar closes.
     The reversal bar must START at or after 10:00 and CLOSE by 14:45.
Exits (Setup B), all detected on 1-minute spot candles; the option is sold at the next minute's
open. If a stop and a target are touched in the same minute, the stop is assumed (worse case).
R19. Half at the middle Bollinger band, half at VWAP, both taken as of the last CLOSED 15-minute
     bar (they move as the day goes on).
R20. Stop 0.05% (BANKNIFTY 0.07%) beyond the pattern's extreme (lowest low / highest high of its
     bars). Skip if the risk > ₹1,500 (BANKNIFTY ₹2,000): risk = |entry spot - stop| x the
     option's delta x quantity.
R21. After the first half is booked, the stop of the other half moves to the entry spot price.
R22. Whatever is open 90 minutes (6 bars) after entry exits at that minute's open. Everything
     (Setup A too) closes at 15:15.

Instrument and risk
R23. BUY the in-the-money option nearest 0.7 delta: call for a long, put for a short, nearest
     weekly expiry (on an expiry day the next one; NIFTY never trades then anyway), 1 lot
     (NIFTY 65, BANKNIFTY 30, flat for all dates as before). Delta: Black-Scholes, with the
     implied volatility of the ATM option of the same type at the entry minute, r = 6.5% (rate
     NOT_VERIFIED; its effect is small). If the ATM option has no price, India VIX is used.
     Settled 2026-11-27: if that strike has no stored candles, the nearest strike up to 2 steps
     away (deeper ITM first) with real candles is used, and the report counts these trades.
R24. At most 2 trades a day and 1 position open at a time.
R25. Day net loss ₹3,000 / week (Mon-Sun) net loss ₹6,000: no new entries after that. Open
     trades are never cut by these (as RL-1.0).
R26. Pass (per index): net profit factor >= 1.10, max drawdown < 20% of ₹1L, >= 60 trades, net
     > 0 in each of the 3 years, and the walk-forward check holds (below).

Both indices together
R27. One open position across both. Both signal in the same minute (same direction): take the
     one stretched more beyond VWAP in standard deviations (Setup A: the bigger gap as a share
     of its own maximum gap).
R28. Same minute, opposite directions: take neither.
R29. Optional, BANKNIFTY Setup B only: a long only if the NIFTY 15-minute bar of the same time
     did not make a new day low (shorts: new day high). No NIFTY bar = no trade. Shown with and
     without. Settled: not applied to Setup A (every first bar makes the day's high and low).
R30. The day and week loss limits and the 2-a-day cap are for the combined account.
R31. Each index must pass on its own (its own trades inside the combined run too).

Walk-forward (the old held-back year is used up, so this replaces it)
- The two thresholds the coordinator scaled for BANKNIFTY and that are most likely to be tuned,
  the gap band (R10) and the trend-day block (R6), get a 3 x 3 grid of multipliers 0.75 / 1 /
  1.25 on the posted values. Anchored: pick the best grid point on year 1, score it on year 2;
  pick again on years 1-2, score it on year 3. "Best" = highest profit factor with at least 20
  trades in the training years (ties: closest to the posted values).
- Holds up = the out-of-sample years 2+3 together have net > 0 and PF >= 1.10.
- The verdict row itself always uses the POSTED values (multiplier 1 / 1); the tuned rows are
  information about stability, not a second chance.

Rows compared (each index on its own, 3 years): MR-4 full (A + B), Setup A only, Setup B only,
BANKNIFTY with R29, and "no-bias days at full size" (information). Then the combined account
(R27-R30) with and without R29, split by index.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

VERSION = "MR-4"
UNDERLYINGS = ("NIFTY", "BANKNIFTY")
VIX_SYMBOL = "NSE-INDIAVIX"
VIX_UNDERLYING = "INDIAVIX"  # store.spot_symbol("INDIAVIX") == VIX_SYMBOL
FUT_SYMBOLS = {"NIFTY": "NSE-NIFTY-FUT-NEAR", "BANKNIFTY": "NSE-BANKNIFTY-FUT-NEAR"}
FIRST_DAY = date(2023, 11, 1)
LAST_DAY = date(2026, 10, 31)
CAPITAL = 100_000.0

VIX_MAX = 20.0
SMA_DAYS = 200
RSI_DAYS = 2
RSI_LONG_BELOW = 30.0
RSI_SHORT_ABOVE = 70.0
HALF_SIZE = 0.5
BB_PERIOD = 20
BB_STD = 2.0
VWAP_STD = 2.0
TARGET_DELTA = 0.7
RISK_FREE = 0.065  # NOT_VERIFIED
MAX_TRADES_DAY = 2
DAY_LOSS = 3000.0
WEEK_LOSS = 6000.0
TIME_STOP_MINUTES = 90

# Pass bar (R26)
PF_MIN = 1.10
MAX_DD_PCT = 20.0
MIN_TRADES = 60
WF_MIN_TRAIN_TRADES = 20
GRID = (0.75, 1.0, 1.25)


@dataclass(frozen=True)
class Params:
    """The index-specific numbers (coordinator's table, 2026-11-20)."""
    underlying: str
    trend_block_pct: float  # R6
    gap_min_pct: float  # R10
    gap_max_pct: float  # R10
    stop_buffer_pct: float  # R20
    max_risk: float  # R20, ₹
    monthly_expiry_only: bool  # R3
    setups: tuple[str, ...] = ("A", "B")
    r29: bool = False
    half_size: float = HALF_SIZE

    def scaled(self, gap: float = 1.0, trend: float = 1.0) -> Params:
        """The walk-forward grid: gap band and trend-day block times a multiplier."""
        return replace(self, gap_min_pct=round(self.gap_min_pct * gap, 4),
                       gap_max_pct=round(self.gap_max_pct * gap, 4),
                       trend_block_pct=round(self.trend_block_pct * trend, 4))


PARAMS = {
    "NIFTY": Params("NIFTY", 0.8, 0.3, 0.8, 0.05, 1500.0, False),
    "BANKNIFTY": Params("BANKNIFTY", 1.1, 0.4, 1.1, 0.07, 2000.0, True),
}
