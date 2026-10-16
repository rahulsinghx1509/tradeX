# TradeX Strategy Specification — `TX-POS-1.0` (DRAFT)

Status: **DRAFT, untested.** Every threshold below is a starting hypothesis. Nothing here is
a proven edge until the backtest (Section 12) and 1–2 months of paper trading say so.
Values marked `TO_CALIBRATE` are placeholders that the backtest or collected data must set.
Values marked `NOT_VERIFIED` depend on data we do not have yet (Section 13).

This is the document the deterministic rule engine and the backtest implement. If code and
this file disagree, the code is wrong or this file must be updated with a new version number.

Read with: `00_MASTER_PROJECT_BRIEF.md`, `01_DECISIONS_AND_CONTEXT.md` (wins over the brief),
`Futures_Tracking_Sheet.xlsx` (metric catalog), `04_LIVE_ALERTS_ROADMAP.md`.

---

## 0. Plain-language summary

- Every evening, after the daily candle closes, TradeX checks all 12 instruments.
- An instrument becomes a **candidate** only if it passes every safety gate: fresh data, no
  results / RBI / budget / corporate action before the planned exit, not in the F&O ban,
  liquid options, enough days to expiry, options not too expensive, and portfolio limits OK.
- It must then show a clear direction on four core checks: trend (EMA 20/50), weekly
  trend, trend strength (ADX) and momentum (RSI). If they disagree: **NO TRADE**.
- Candidates are scored 0–100 on confirming evidence (futures OI buildup, volume, relative
  strength, option-chain OI, volatility, how stretched price is). Score ≥ 60 is needed.
- The best candidate gets a **bull call spread** (long) or **bear put spread** (short) on
  the nearest monthly expiry with ≥ 20 days left. Size is set so that hitting the stop
  loses at most 1–3% of your total capital.
- You get a Telegram **SETUP** alert in the evening and an **ENTER** alert the next morning
  (09:45–11:30) only if conditions still hold. Exits come as **EXIT** alerts: stop on the
  underlying, premium stop, target, trailing stop, time stop, event, or 7 days to expiry.
- "No trade" is the normal answer on most days. The engine does not aim for a number of
  trades per week; trades happen when conditions are met.

### Who reads the charts

The alerts come from the **TradeX rule engine**, a Python program that runs on the server
during market hours and applies the fixed rules in this file. **Claude does not watch your
charts live and does not decide trades.** Claude's job is to build the engine, test it,
review its results with you, and propose rule changes (as a new version, backtested before
use). "Reading the candles" happens through the defined metrics below (trend, swings, ATR,
VWAP, volume, OI). Candlestick patterns (engulfing, doji, etc.) are not used in v1 because
they are hard to define and have weak evidence; one can be added later as a tested rule.

---

## 1. Decisions RK needs to confirm

### 1.1 Where your plan conflicts with the agreed risk rules

| # | Your plan | Agreed rule | Conflict | Default in this spec |
|---|---|---|---|---|
| C1 | **₹20,000 per trade** | Max loss 1–3% of capital per trade (₹1,000–3,000 on ₹1L) | On a debit spread the **premium paid is the true worst case** (a gap can jump past any stop). ₹20,000 deployed is 20% of ₹1L and 40% of ₹50k. To keep the loss at the stop to 2% of ₹1L (₹2,000), the stop would have to sit only 10% below entry — far too tight for options, so it would be hit by normal noise. | ₹20,000 is a **cap** on premium deployed, not a target. The stop limits the planned loss to `risk_pct` of total capital (default 2%). In practice the engine will usually deploy **₹5,000–₹10,000** per trade. Worst-case (gap) loss is additionally capped at 20% of capital. |
| C2 | **2–3 trades a week** | Max 2 open trades; positional (days to weeks) | Positional trades last 5–15 sessions. With at most 2 open at once, 2–3 **new** trades a week is not possible most weeks. Realistic: about 2–5 trades a month, and some months fewer. | Max 2 open trades kept. Max 1 new entry per day. No weekly target: frequency is an output of the rules. |
| C3 | ₹20k × 2–3 trades | Monthly loss limit ~₹10,000 | Three full ₹20k positions hitting a gap-down could lose ₹30k+ in a week, three times the monthly limit. At ₹50k capital, ₹10,000 is 20% of the account in one month. | Monthly limit ₹10,000 **or 10% of capital, whichever is lower**. Hit it ⇒ no new entries until next month. Cumulative loss ₹25,000 ⇒ back to paper trading. |
| C4 | "Claude will guide me by reading the charts" | No LLM as decision-maker; deterministic rules | Claude cannot watch the market live, and an LLM's chart reading cannot be backtested or repeated. | Alerts come from the rule engine. Claude reviews performance with you (e.g. weekly) and proposes changes as new rule versions. |
| C5 | "Use all data and metrics" | Avoid double-counting; every rule needs a formula, threshold and test | Using ~80 metrics as votes overfits (looks great in a backtest, fails live) and counts the same thing many times (EMA, MACD and ROC all measure trend). | **Every one of the 81 catalog metrics has a role** (Section 14): hard gate, core direction, score, strike/expiry, sizing, exit, or context shown in the alert. Only one metric per "family" votes on direction. |

### 1.2 Values to confirm (defaults in bold)

| Setting | Default | Range | Why it matters |
|---|---|---|---|
| `capital_inr` | **₹1,00,000** | ₹50k–₹1L | All risk limits are % of this. Update it as the account changes. |
| `risk_pct_per_trade` | **2%** | 1–3% | Loss at the stop. 1% ≈ ₹1,000 can make even 1 BANKNIFTY spread lot too large (see Section 8 example). |
| `max_debit_per_trade_inr` | **₹20,000** | — | Your ₹20k, used as a cap. |
| `max_debit_pct_of_capital` | **20%** | 10–20% | Worst case if price gaps past the stop. 20% of ₹50k = ₹10k. |
| `max_open_trades` | **2** | 1–2 | Agreed. |
| `monthly_loss_limit_inr` | **min(₹10,000, 10% of capital)** | — | Agreed ~₹10k, scaled down for smaller capital. |
| `paper_reset_loss_inr` | **₹25,000** | — | Agreed. |
| Structure | **Debit spreads only** | — | Agreed preference. Single-leg buying stays off in v1. |

---

## 2. When the engine runs

All times IST. Storage is UTC (see `backend/app/core/market_time.py`). Holidays come from
the NSE holiday calendar.

| Run | Time | Uses | Output |
|---|---|---|---|
| **EOD scan** | After 15:30, once the daily candle is complete and validated (target 16:00) | Completed daily + weekly candles, end-of-day option chain, futures OI, NSE daily files (ban list, events) | `SETUP` alert per selected candidate, or a daily `NO TRADE` summary with reasons |
| **Entry check** | Every collector cycle 09:45–11:30 next session | Live quote, option chain, 1-hour candles, futures VWAP | `ENTER` or `SETUP EXPIRED` |
| **Position monitor** | Every collector cycle 09:15–15:30 while a trade is open | Live underlying, live spread bid/ask, completed 1-hour candles | `EXIT`, `STOP MOVED`, `DATA PROBLEM` |
| **EOD position review** | With the EOD scan | Completed daily candle | Trailing stop update, time stop, reversal, new events |

No look-ahead: the backtest must use exactly the same timing. A signal built from the daily
candle of day D can only be entered on day D+1, at a price available on D+1.

---

## 3. Universe and selection

Pool (from decisions file): NIFTY, BANKNIFTY, HDFCBANK, ICICIBANK, SBIN, AXISBANK, INFY,
TCS, RELIANCE, BHARTIARTL, LT, TATAMOTORS (confirm the post-demerger F&O symbol from the
Groww instrument list).

Correlation clusters (count as **one bet** in the same direction):

| Cluster | Members |
|---|---|
| `INDEX_BANK` | NIFTY, BANKNIFTY, HDFCBANK, ICICIBANK, SBIN, AXISBANK |
| `IT` | INFY, TCS |
| Others | each alone: RELIANCE, BHARTIARTL, LT, TATAMOTORS |

The static clusters are the default. The backtest also computes 60-day return correlation;
any pair ≥ 0.70 (`TO_CALIBRATE`) is treated as the same cluster for that day.

**Ranking:** among instruments that pass Sections 4–6, sort by score (desc), then relative
strength vs NIFTY in the trade direction, then option liquidity. Take the top one that does
not break a portfolio limit. At most **1 new entry per day**.

---

## 4. Layer 1 — Hard gates (any failure ⇒ NO TRADE for that instrument)

A gate whose input is **missing** fails (fail-closed). Missing never becomes zero.

| ID | Gate | Rule (pass condition) | Data status |
|---|---|---|---|
| G1 | Data freshness | Latest daily candle is today's (EOD scan) / live quote ≤ 2 collector intervals old (entry, monitor) | Built (freshness.py) |
| G2 | Data quality | No INVALID candles in the last 250 daily bars; no gaps; ≥ 250 daily and ≥ 60 weekly candles for EMA/ADX warm-up | Built (data_quality.py) |
| G3 | Real data | `is_sample = FALSE` for any real alert (sample data can only produce alerts marked SAMPLE) | Built |
| G4 | Instrument mapping | Underlying, future and both option legs map to the latest instrument list; one expiry per series; lot size read from the list, never hard-coded | Partly built |
| G5 | Results date (stocks) | No results date between entry and **planned exit date** (Section 9). Unknown results date ⇒ fail | **Missing** — NSE corporate announcements |
| G6 | Macro events | No RBI policy, Union Budget, national election result, or exchange-declared special session between entry and planned exit. US Fed decision = risk flag only | **Missing** — events table to be maintained |
| G6b | Event exception | If a G5/G6 event is ≥ 6 trading days away, entry is allowed but the planned exit becomes the session **before** the event | — |
| G7 | Corporate actions | No ex-dividend, split, bonus or rights date for the stock between entry and planned exit | **Missing** — NSE corporate actions |
| G8 | F&O ban | Stock not in ban; market-wide position limit use < 85% (`TO_CALIBRATE`) | **Missing** — NSE daily ban file |
| G9 | Days to expiry | Chosen expiry has DTE ≥ 20 calendar days (`min_entry_dte`, range 15–25) | Built (from instrument list) |
| G10 | Option liquidity | For **each leg**: bid-ask spread ≤ 2% of mid (index) / ≤ 4% (stock); OI at strike ≥ `min_leg_oi`; today's volume ≥ `min_leg_volume` (both `TO_CALIBRATE` from collected chains) | **NOT_VERIFIED** — depth/bid-ask in Groww chain |
| G11 | Option cost | ATM IV ÷ 20-day HV ≤ 1.5 (`max_iv_hv`). Once 1 year of stored IV exists: IV Rank ≤ 80 | IV NOT_VERIFIED; IV Rank **needs ~1 year of our own IV history** |
| G12 | Volatility shock | India VIX 5-day change ≤ +20% (`TO_CALIBRATE`) | **NOT_VERIFIED** — VIX availability |
| G13 | Break-even vs expected move | Distance from entry to the spread's break-even ≤ 0.6 × expected move to the planned exit date (`TO_CALIBRATE`) | Needs option chain |
| G14 | Reward:risk | Modelled ₹ reward at target ÷ modelled ₹ loss at stop ≥ 2.0 (agreed ≥ 2R; if the backtest shows almost nothing passes, RK decides whether to lower it) | Needs option chain |
| G15 | Portfolio | Open trades < 2; no open trade in the same cluster and direction; month-to-date loss (realised + open at stop) < monthly limit; cumulative loss < ₹25,000; total premium across open trades ≤ 40% of capital | Needs My Trades / paper ledger |
| G16 | Cool-down | No stop-out on this instrument in the last 3 sessions | Needs paper ledger |
| G17 | Sizing | Position sizing (Section 8) gives ≥ 1 lot | — |

Note on the catalog: the "Expected Move" row says *"If target is smaller than expected move,
the buy has poor odds"*. For a buyer it is the other way round: the trade has poor odds when
the move **needed** (to break-even or target) is **larger** than the expected move. G13 uses
the corrected reading.

---

## 5. Layer 2 — Core direction (all four must agree)

One metric per family, all on **completed** candles of the underlying (index value for
NIFTY/BANKNIFTY, cash price for stocks).

| ID | Family | LONG requires | SHORT requires |
|---|---|---|---|
| D1 | Daily trend | Close > EMA50 **and** EMA20 > EMA50 | Close < EMA50 **and** EMA20 < EMA50 |
| D2 | Weekly trend | Weekly close > weekly EMA20 | Weekly close < weekly EMA20 |
| D3 | Trend strength | ADX(14) ≥ 20 **and** +DI > −DI | ADX(14) ≥ 20 **and** −DI > +DI |
| D4 | Momentum | 50 ≤ RSI(14) ≤ 70 | 30 ≤ RSI(14) ≤ 50 |

Anything else ⇒ `NO TRADE` with the failing checks listed. RSI > 70 (long) or < 30 (short)
is treated as "too late to chase", not as a reversal signal.

---

## 6. Layer 3 — Confirmation score (0–100, need ≥ 60)

Points below are for a LONG; a SHORT uses the mirror image. Weights are starting values
(`TO_CALIBRATE`); the backtest may change them in a new version, never silently.

| ID | Component | Points (LONG) | Max | Data |
|---|---|---|---|---|
| S1 | Futures positioning (near-month future, daily) | Long buildup +20 · Short covering +8 · Long unwinding 0 · Short buildup −20. Counts only if \|OI change\| ≥ 2% (`TO_CALIBRATE`) and volume ≥ 0.8× 20-day average (filters volume-OI divergence) | 20 | NOT_VERIFIED (futures OI) |
| S2 | Relative volume | Futures volume ÷ 20-day average ≥ 1.2 on the signal day: +10 | 10 | NOT_VERIFIED |
| S3 | Relative strength | Stocks: 20-day return vs NIFTY in the top 3 of the pool: +15, top half: +8. BANKNIFTY: 20-day RS vs NIFTY > 1: +10. NIFTY: n/a (component dropped) | 15 | Built from candles once stocks are ingested |
| S4 | Room to target (option chain) | No call-OI "wall" (strike with the highest call OI) between entry and target: +10; wall inside: −10 | 10 | NOT_VERIFIED (chain) |
| S5 | Fresh put writing | Put OI increased at or just below ATM vs previous day: +10 | 10 | Needs daily chain snapshots stored |
| S6 | Not stretched | Close within 1.5 ATR of EMA20: +10; more than 2.5 ATR away: −15 | 10 | Built from candles |
| S7 | Long-term regime | Close > EMA200: +5 | 5 | Built from candles |
| S8 | Volatility backdrop | India VIX 5-day change ≤ 0: +10; between 0 and +20%: 0 | 10 | NOT_VERIFIED (VIX) |
| S9 | Institutional flow (indices only) | FII index-futures net long increased over 3 days: +10 | 10 | **Missing** — NSE participant-wise OI |

**Missing data rule:** `score = 100 × earned ÷ max points of the components that have data`.
If the components with data are worth less than 70% of the full maximum, the score is
`INSUFFICIENT_DATA` ⇒ NO TRADE. The alert always lists which components were missing.

A score is a ranking, **not a probability** (brief §6.8). No "% confidence" is shown until
calibrated out of sample.

---

## 7. Strike and expiry selection

| Item | Rule |
|---|---|
| Structure | LONG ⇒ **bull call spread** (buy lower-strike CE, sell higher-strike CE). SHORT ⇒ **bear put spread** (buy higher-strike PE, sell lower-strike PE). Same expiry for both legs. |
| Expiry | Nearest **monthly** expiry with DTE ≥ 20 calendar days; otherwise the next monthly. BANKNIFTY has monthly expiries only. Expiry dates and weekdays come from the instrument list, never assumed. |
| Long leg | Strike with delta 0.50–0.60 (ATM or one strike in the money). Fallback if Greeks missing: ATM strike, with delta computed by Black-76 from the chain IV. |
| Short leg | Strike nearest the **target** (Section 9), and with delta 0.25–0.35. If the two disagree, use the strike nearest the target. |
| Width check | Net debit ≤ 50% of strike width (so max profit ≥ max loss at expiry). Prefer ≤ 45%. |
| Strike interval | Read from the chain (do not assume 100-point BANKNIFTY strikes). |
| Leg order (for when orders exist) | Buy the long leg first, then sell the short leg. Never leave a naked short. |

---

## 8. Position sizing

Definitions (all in ₹, per lot unless noted):

```
risk_budget        = capital_inr × risk_pct_per_trade                  # e.g. 1,00,000 × 2% = 2,000
debit_per_lot      = net_debit_points × lot_size
value_at_stop      = modelled spread value if the underlying hits the stop
                     (Black-76 with current leg IVs and time to the expected stop date;
                      fallback: debit × (1 − premium_stop_pct))
loss_per_lot       = (net_debit_points − value_at_stop_points) × lot_size + round_trip_costs_per_lot
lots_by_risk       = floor(risk_budget / loss_per_lot)
lots_by_cap        = floor(min(max_debit_per_trade_inr,
                               max_debit_pct_of_capital × capital_inr) / debit_per_lot)
lots               = min(lots_by_risk, lots_by_cap)
if lots == 0  ->  NO TRADE ("spread too large for risk budget")
```

`round_trip_costs_per_lot` = brokerage + STT + exchange charges + SEBI fee + stamp duty +
GST for 4 orders (2 legs in, 2 out) + slippage of half the bid-ask spread per leg. Charge
rates come from Groww's charges page (`NOT_VERIFIED`, store them as config with a date).
Fixed per-order brokerage matters on small spreads, which is why costs are inside the sizing.

**Worked example — illustrative numbers, not market data.**
Capital ₹1,00,000; BANKNIFTY lot 30 (from the decisions file; read the live value).
Bull call spread with a 600-point width bought for 220 points ⇒ debit ₹6,600 per lot, max
profit (600 − 220) × 30 = ₹11,400. Modelled spread value at the stop = 150 points ⇒ loss
70 × 30 = ₹2,100 + ₹150 costs = ₹2,250 per lot.
- At 2% risk (₹2,000): `lots_by_risk = 0` ⇒ **NO TRADE**.
- At 3% risk (₹3,000): 1 lot, ₹6,600 deployed, planned loss ₹2,250, worst case ₹6,600.

So with ₹1L, one BANKNIFTY spread lot sits right at the edge of the agreed risk range, and
a full ₹20,000 deployment would only fit with a much wider risk than agreed. Stock spreads
with smaller lot values will fit more often.

Margin: before an `ENTER` alert, check margin needed for the spread with Groww's margin API
(`NOT_VERIFIED`); total margin + premium across open trades ≤ 50% of capital.

---

## 9. Entry, stop, target and planned exit levels

Levels are on the **underlying** (index value or stock cash price), using daily ATR(14).

| Level | LONG rule (SHORT mirrors) |
|---|---|
| Entry reference | Underlying price when the `ENTER` alert fires |
| Initial stop | Lowest low of the last 10 sessions − 0.25 ATR. If that is more than 2.0 ATR below entry, use entry − 2.0 ATR. If less than 1.0 ATR below entry, use entry − 1.0 ATR. |
| R | Entry − initial stop (underlying points) |
| Target | Entry + 2R. If the highest-call-OI strike or a 60-session swing high is closer, the trade fails S4/G14 instead of shrinking the target. |
| Planned exit date | Earliest of: entry + 10 trading days (`max_hold_days`, `TO_CALIBRATE`), the session before any G6b event, the day DTE reaches 7. |

### Entry trigger (next session, 09:45–11:30)

All must hold at the check:
1. Gates G1–G17 still pass on live data.
2. Underlying has not moved more than 0.75 ATR beyond the signal-day close in the trade
   direction (no chasing) and has not touched the initial stop.
3. LONG: underlying futures price > today's futures VWAP (SHORT: below).
4. Live spread debit ≤ 105% of the debit used for sizing; otherwise re-size, and if the
   re-sized trade fails G14 or G17, skip.

If not triggered by 11:30 the setup expires (`SETUP EXPIRED`). It can re-qualify at the next
EOD scan.

---

## 10. Exit rules (first one hit wins)

| ID | Exit | Rule | Checked |
|---|---|---|---|
| X1 | Underlying stop | A **completed 1-hour candle** closes beyond the current stop (initial or trailing) | Each cycle |
| X2 | Premium stop | Spread mid ≤ 60% of entry debit (40% loss, `premium_stop_pct` 30–40%) on 2 consecutive checks | Each cycle |
| X3 | Profit take | Spread mid ≥ 80% of strike width (little left to gain, risk not worth holding) | Each cycle |
| X4 | Trailing stop | After price reaches +1R: stop moves to entry (break-even). After +1.5R: stop = highest high since entry − 2.5 ATR (chandelier). Stop only moves in the trade's favour. Alternatives to compare in the backtest: daily close below EMA20; 3-session swing low. | EOD (and X1 checks it intraday) |
| X5 | Time stop | After 5 trading days, if the best move so far is < +0.5R ⇒ exit | EOD |
| X6 | Expiry stop | Exit when DTE ≤ 7 calendar days, by 15:00 on that session at the latest | Each cycle |
| X7 | Event stop | Exit by 15:00 on the session before a G5/G6/G7 event, including one added to the calendar after entry | EOD + each cycle |
| X8 | Trend reversal | Daily close back below EMA50 (LONG) or the core direction (Section 5) flips | EOD |
| X9 | Ban list | Stock enters the F&O ban ⇒ exit alert (only reducing is allowed during a ban) | EOD |

Portfolio stops (monthly limit, ₹25k) block **new** entries; open trades keep their own stops.

**Data problem while in a trade:** the engine never exits on stale or invalid data. It sends
a `DATA PROBLEM` alert ("data stale since HH:MM — manage manually, your stop is X"). Because
v1 places no orders, keep a stop (e.g. a GTT order) at the broker yourself for each real trade.

---

## 11. Alerts (Telegram)

Every alert carries: instrument, direction, rule version, signal time, source-data time,
score with components (and which were missing), risk flags, and `PAPER` or `REAL`.

```
SETUP  BANKNIFTY  LONG  score 72/100  (TX-POS-1.0)
Plan: bull call spread 57000/57600 CE, expiry 25-Nov, DTE 40
Entry window tomorrow 09:45–11:30 if price ≤ 57,380 and above VWAP
Stop 56,450 (underlying) · Target 58,250 · Planned exit by 05-Dec
Size 1 lot · debit ≈ ₹6,600 · loss at stop ≈ ₹2,250 · worst case ₹6,600
Missing: S9 FII flow · Flags: Fed decision 06-Nov (flag only)

ENTER  BANKNIFTY bull call spread 57000/57600 CE 25-Nov, max debit 231
EXIT   BANKNIFTY  reason X4 trailing stop  ·  P&L after costs ₹+3,140 (PAPER)
STOP MOVED  BANKNIFTY  new stop 57,020 (break-even)
NO TRADE today: 12 checked · 7 failed core direction · 3 failed gates · 2 scored < 60
```
(Numbers in the sample are illustrative.)

Each alert is a row in `trading_signals` (brief §11.6) with `rule_version`,
`supporting_factors`, `contradicting_factors`, `risk_flags` and `data_quality_status`.

---

## 12. Backtest and paper-trading plan

1. **Data first.** The biggest open question is whether Groww gives history for **expired
   option contracts** (Milestone 0).
   - If yes: backtest on real historical spread prices.
   - If no: backtest the underlying signals for real, and price the spreads with Black-76
     using HV-based IV (labelled `MODELLED_OPTION_PRICES`). This is an approximation; paper
     trading on real chain prices becomes the main test.
2. **Rules:** same timing as live (Section 2), no look-ahead, costs and slippage included,
   expiry handled per contract, lot sizes as they were at the time.
3. **Split:** first ~60% of history to develop, the rest untouched for validation, then
   walk-forward (re-fit on a rolling window, test on the next block).
4. **Report:** number of trades, win rate, average win/loss, expectancy per trade in ₹ and
   in R, profit factor, max drawdown, longest losing streak, MAE/MFE, results by instrument,
   by direction and by exit reason, and how often each gate blocked a trade.
5. **Compare** the three trailing methods (X4) and premium stop 30% vs 40%.
6. **Pass bar to move to paper trading** (proposed, RK to agree before results are seen):
   ≥ 30 trades in validation, expectancy > 0 after costs, profit factor ≥ 1.3, max drawdown
   ≤ 15% of capital. Fewer than 30 trades means "not enough evidence", not "pass".
7. **Paper trading** 1–2 months: every `ENTER`/`EXIT` alert is booked at the live bid/ask.
   Move to real money (small size) only with RK's explicit approval.

---

## 13. Data we need but do not have yet

| Data | Needed for | Source | Status |
|---|---|---|---|
| Expired option contract history | Realistic backtest | Groww | NOT_VERIFIED (Milestone 0) |
| Option chain with Greeks, IV, bid/ask, OI, volume | G10, G11, G13, G14, S4, S5, Section 7 | Groww `get_option_chain` / quote | NOT_VERIFIED |
| Daily chain snapshots (strike OI change) | S5 | Self-collected daily | Not built |
| ATM IV history (1 year) | IV Rank in G11 | Self-collected daily | Not built; IV Rank unusable for ~1 year — G11 uses IV/HV meanwhile |
| India VIX | G12, S8 | Groww (`INDIAVIX`?) or NSE | NOT_VERIFIED |
| Futures OI and volume (index and stock futures) | S1, S2, VWAP in entry trigger | Groww candles | NOT_VERIFIED |
| Stock candles (10 stocks) | All stock signals, S3 | Groww | Milestone 2 |
| Results dates | G5 | NSE corporate announcements | Not built |
| Corporate actions | G7 | NSE corporate actions | Not built |
| F&O ban list + MWPL % | G8, X9 | NSE daily reports | Not built |
| Macro event calendar | G6 | Maintained in DB (RBI, Budget, elections, Fed) | Not built |
| FII/DII participant-wise OI | S9 | NSE daily reports | Not built |
| Charges schedule | Sizing, backtest | Groww charges page | NOT_VERIFIED |
| Margin for spreads | Section 8 | Groww margin API | NOT_VERIFIED |
| Global proxies (GIFT Nifty, US indices, crude, USD/INR, US 10Y) | Context only | Source not chosen | Not built |
| Paper / real trade ledger | G15, G16 | TradeX "My Trades" | Not built |

Until a gate's data exists the gate **fails**, so real alerts for stocks cannot start before
the results-date, corporate-action and ban-list feeds are in. Index trades need G6 (macro
calendar) and G10–G14 (option chain).

---

## 14. Role of every catalog metric

Roles: **GATE** (blocks), **CORE** (direction vote), **SCORE**, **STRIKE**, **SIZING**,
**EXIT**, **PORTFOLIO**, **CONTEXT** (shown in the alert/dashboard, no effect on the
decision in v1), **NOT USED** (not relevant to debit spreads in v1).

| Category | Metric | Role | Where |
|---|---|---|---|
| Price & Basis | Spot Price | CORE input | §5, §9 |
| | Futures Price | SCORE input, entry VWAP | S1, §9 |
| | Basis | CONTEXT | |
| | Basis Trend | CONTEXT | |
| | Fair Value | NOT USED | futures only |
| | Mispricing % | NOT USED | futures only |
| Open Interest | Open Interest | SCORE | S1 |
| | OI Change | SCORE | S1 |
| | Long Buildup | SCORE | S1 |
| | Short Buildup | SCORE | S1 |
| | Short Covering | SCORE | S1 |
| | Long Unwinding | SCORE; CONTEXT warning while in a long | S1 |
| | OI Concentration | CONTEXT | |
| Volume | Daily Traded Volume | GATE (DQ), SCORE input | G2, S1 |
| | Volume vs Average | SCORE | S2 |
| | Volume-OI Divergence | SCORE (filter on S1) | S1 |
| Rollover | Rollover % | CONTEXT | we exit before the last week |
| | Market-Wide Rollover | CONTEXT | |
| | Rollover Cost | CONTEXT | |
| Carry / Margin | Cost of Carry | NOT USED | futures only |
| | SPAN Margin | SIZING | §8 margin check |
| | Exposure Margin | SIZING | §8 margin check |
| | Margin Utilization % | PORTFOLIO | §8 (≤ 50%) |
| | Mark-to-Market | CONTEXT | My Trades |
| Volatility | Historical Volatility | GATE, pricing | G11, §8 |
| | Implied Volatility | GATE, pricing | G11, §7, §8 |
| | IV Percentile / Rank | GATE (after 1 year of data) | G11 |
| | ATR | SIZING, EXIT | §9, X4, S6 |
| Trend & Technicals | Moving Averages (20/50/200) | CORE (20/50), SCORE (200), EXIT | D1, S6, S7, X8 |
| | RSI | CORE | D4 |
| | MACD | CONTEXT | duplicates the EMA trend |
| | VWAP | Entry trigger | §9 |
| | Support / Resistance | Stop and target placement, SCORE | §9, S4 |
| Sentiment | PCR | CONTEXT | brief: never decides direction |
| | India VIX | GATE, SCORE | G12, S8 |
| | FII/DII Futures Positioning | SCORE (indices) | S9 |
| | Sector / Index Correlation | PORTFOLIO | §3 clusters, G15 |
| Fundamental / Event | Earnings Date | GATE, EXIT | G5, X7 |
| | Corporate Actions | GATE, EXIT | G7, X7 |
| | Macro Events | GATE, EXIT | G6, X7 |
| | F&O Ban List Status | GATE, EXIT | G8, X9 |
| Risk & Sizing | Risk per Trade % | SIZING | §8 |
| | Position Size | SIZING | §8 |
| | Risk-Reward Ratio | GATE | G14 |
| | Leverage Ratio | NOT USED | futures only |
| | Max Drawdown Tolerance | PORTFOLIO | ₹25k ⇒ paper |
| Option Greeks | Delta | STRIKE | §7 |
| | Gamma | EXIT rationale | X6 (exit before last week) |
| | Theta | STRIKE (shown, inside pricing) | §8, X5 |
| | Vega | CONTEXT risk flag | event IV crush |
| IV Analysis | IV vs HV | GATE | G11 |
| | Expected Move | GATE | G13 |
| | IV Skew | CONTEXT | |
| | Event IV Crush Risk | GATE | G5–G7 |
| | IV History (daily store) | Data for GATE | G11 |
| Option Chain | Strike-wise OI | SCORE, target check | S4, §9 |
| | Strike-wise OI Change | SCORE | S5 |
| | Max Pain | CONTEXT | never standalone |
| | Days to Expiry | GATE, EXIT | G9, X6 |
| | Option Liquidity | GATE | G10 |
| Trend Strength | ADX | CORE | D3 |
| | Weekly / Multi-Timeframe Trend | CORE | D2 |
| | Relative Strength vs NIFTY | SCORE, ranking | S3, §3 |
| Global / Macro | GIFT Nifty | CONTEXT (gap warning) | |
| | US Indices | CONTEXT (IT stocks flag) | |
| | Crude Oil | CONTEXT | |
| | USD/INR | CONTEXT | |
| | US 10-Year Yield | CONTEXT | |
| Trade Management | Break-even Price | GATE | G13 |
| | Premium Stop-Loss | EXIT | X2 |
| | Time Stop | EXIT | X5 |
| | Trailing Stop | EXIT | X4 |
| | Spread Max Profit / Loss | SIZING, alert | §8, §11 |
| | Transaction Costs & Slippage | SIZING, backtest | §8, §12 |
| Portfolio | Max Open Positions | PORTFOLIO | G15 |
| | Correlated Exposure Limit | PORTFOLIO | §3, G15 |
| | Monthly Loss Limit | PORTFOLIO | G15 |
| Data Quality | Data Freshness | GATE | G1 |
| | Missing / Duplicate Candles | GATE | G2 |
| | Invalid OHLC | GATE | G2 |
| | Instrument & Expiry Mapping | GATE | G4 |

Context metrics can be promoted to SCORE or GATE in a later version, but only after the
backtest shows they add something beyond the existing rules.

---

## 15. Configuration (what the engine reads)

All thresholds live in one versioned config (e.g. `backend/app/strategy/tx_pos_1_0.yaml`),
not scattered in code. Changing a value means a new `rule_version`.

```yaml
rule_version: TX-POS-1.0
capital_inr: 100000
risk_pct_per_trade: 0.02
max_debit_per_trade_inr: 20000
max_debit_pct_of_capital: 0.20
max_total_debit_pct: 0.40
max_margin_pct: 0.50
max_open_trades: 2
max_new_entries_per_day: 1
monthly_loss_limit_inr: 10000        # engine uses min(this, 10% of capital)
monthly_loss_limit_pct: 0.10
paper_reset_loss_inr: 25000
cooldown_sessions_after_stop: 3
min_entry_dte: 20
exit_dte: 7
max_hold_days: 10
time_stop_days: 5
time_stop_min_r: 0.5
ema_fast: 20
ema_slow: 50
ema_regime: 200
weekly_ema: 20
adx_period: 14
adx_min: 20
rsi_period: 14
rsi_long: [50, 70]
rsi_short: [30, 50]
atr_period: 14
stop_swing_lookback: 10
stop_buffer_atr: 0.25
stop_min_atr: 1.0
stop_max_atr: 2.0
target_r: 2.0
min_reward_risk: 2.0
breakeven_max_expected_move: 0.6
trail_breakeven_at_r: 1.0
trail_start_at_r: 1.5
trail_atr_mult: 2.5
premium_stop_pct: 0.40
profit_take_pct_of_width: 0.80
long_leg_delta: [0.50, 0.60]
short_leg_delta: [0.25, 0.35]
max_debit_pct_of_width: 0.50
max_iv_hv: 1.5
max_iv_rank: 80
max_vix_5d_change: 0.20
max_bid_ask_pct_index: 0.02
max_bid_ask_pct_stock: 0.04
min_leg_oi: TO_CALIBRATE
min_leg_volume: TO_CALIBRATE
mwpl_block_pct: 0.85
max_chase_atr: 0.75
entry_window: ["09:45", "11:30"]
min_score: 60
min_score_coverage: 0.70
correlation_cluster_threshold: 0.70
```

---

## 16. Evaluation order (pseudocode)

```
for each instrument in pool:
    if any gate G1–G13 fails:           record NO TRADE (reason = gate ids); continue
    direction = core_direction(D1..D4)
    if direction is None:               record NO TRADE (reason = failing D checks); continue
    score = confirmation_score(S1..S9)
    if score is INSUFFICIENT_DATA or score < min_score:
                                        record NO TRADE; continue
    spread = pick_spread(direction)     # Section 7
    levels = stop_target_exit(spread)   # Section 9
    if G14 fails or sizing gives 0 lots: record NO TRADE; continue
    candidates.append(...)
rank candidates; take the first that passes G15–G17  ->  SETUP alert
everything else                                       ->  daily NO TRADE summary
```

## Changelog

- `TX-POS-1.0` (2026-10-16): first draft for review. Not backtested.
