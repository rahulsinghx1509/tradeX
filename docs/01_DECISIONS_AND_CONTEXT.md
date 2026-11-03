# Decisions and Context (from planning discussions)

This file records what was decided after the master brief was written.
Where it differs from `00_MASTER_PROJECT_BRIEF.md`, **this file wins**.

Last updated: 2026-10-15

---

## 1. Trading profile

| Topic | Decision |
|---|---|
| Style | **Positional** (days to weeks). Intraday is not a focus. |
| Instruments traded | **Options** (buying, preferably debit spreads). Futures later, when capital allows. |
| Capital | ₹50,000 – ₹1,00,000 |
| Risk per trade | Owner initially said 5–10%; agreed guidance is professional practice: **1–3% max loss per trade** (₹1,000–3,000 on ₹1L), target ≥ 2× risk. Make it a configurable setting. |
| Portfolio limits | Max 2 open trades; monthly loss limit ~₹10,000; stop real trading and return to paper if down ~₹25,000. |
| Alerts | **Telegram** |
| Decision needed | Exact **entry and exit** (direction, strike, expiry, entry, stop, target, trailing stop). |

### Why options, not futures (with ₹1L)
- SEBI minimum F&O contract value ≈ ₹15 lakh. No "small lot" futures exist.
- BANKNIFTY lot = **30** (since Jan 2026; NIFTY = 65). Futures margin ≈ ₹1.8–2.2L — more than total capital.
- A sensible BANKNIFTY futures stop (~1.5%) ≈ ₹25,000 risk ⇒ needs ~₹12–13L capital at 2% risk.
- Lot sizes are revised ~every 6 months — always read them from the provider instrument list, never hard-code.

## 2. Instrument pool (12)

| Group | Instruments |
|---|---|
| Indices | NIFTY, BANKNIFTY |
| Banking | HDFCBANK, ICICIBANK, SBIN, AXISBANK |
| IT | INFY, TCS |
| Others | RELIANCE, BHARTIARTL, LT, TATAMOTORS |

Notes:
- 6 of 12 are bank-heavy ⇒ needs a **correlated exposure limit**.
- TATAMOTORS was demerged in late 2025; confirm current F&O symbol from Groww's instrument list.
- BANKNIFTY options have **monthly expiries only**.
- Milestone 1 starts with NIFTY + BANKNIFTY; stocks come in Milestone 2.
- 2026-10-17: the dashboard tracks all 12 (`UNDERLYINGS` in `.env`), with **FINNIFTY in place of TATAMOTORS** until its post-demerger F&O symbol is confirmed. Symbols missing from Groww's instrument list are skipped with a warning.

## 3. Roadmap (agreed order)

```
✅ Repo + metric catalog
👉 Foundation docs
   Milestone 0: verify Groww data (capability matrix)
   Milestone 1: data → PostgreSQL → FastAPI → dashboard (sample data first, clearly labelled)
   Milestone 2: core metrics (incl. options metrics: Greeks, IV vs HV, expected move, strike OI)
   "My Trades" page (read-only positions/orders/P&L from Groww, PAPER vs REAL labels)
   Telegram alerts
   Strategy spec → backtest → paper trade (1–2 months)
   Real options trading, small size
```

Defaults for Milestone 1: NIFTY + BANKNIFTY; daily + 1-hour timeframes; as much history as Groww allows; local/dev first, cloud only after owner approves cost.

## 4. Data sources

- **Primary:** Groww Trade API (paid plan ~₹499/month + tax). Owner is buying it.
- **Free supplements:** NSE daily reports (participant-wise OI / FII-DII, F&O ban list, corporate actions), economic calendar maintained in DB, a free global-data API for crude / USD-INR / US indices / yields (to be chosen).
- NSE sells data only to institutions/vendors; not needed. Paid vendors (TrueData, Global Datafeeds) or other broker APIs are fallbacks.
- No scraping or bypassing access restrictions.

### Groww — what is known (all NOT_VERIFIED until tested on owner's account)
- Quote includes `open_interest`, `previous_open_interest`, `oi_day_change_percentage`, `implied_volatility`, depth.
- Option chain includes Greeks.
- Historical candles: OHLC + volume + OI (FNO). Per-request windows: 1–5 min → 30 days; 10–30 min → 90 days; 1h–1M → 180 days. Older historical endpoint is deprecated — use the backtesting "historical candles" API.
- Portfolio / positions / orders / margin APIs exist.
- Rate limits reported ~10 req/s, 300/min for live data (sources conflict — verify).
- **Biggest open question:** historical data for **expired option contracts** (decides how option strategies are backtested).
- India VIX availability not confirmed.
- IV history not provided ⇒ store ATM IV daily ourselves (IV Rank needs ~1 year).

## 5. Signal & exit concepts agreed

- Starting hypothesis (untested): bullish when price > 50 EMA, 20 EMA > 50 EMA, ADX > 20, RSI 50–70, long buildup, no major event soon ⇒ bull call spread. Mirror for bearish. Otherwise NO TRADE.
- Positional option rules: monthly expiry with ≥15–20 days left; exit before last week; premium stop (30–40%); time stop.
- **Trailing stop-loss**: trail on the underlying (ATR / swing low / EMA), premium stop as backup. Methods to be compared by backtest.
- Paper trading before real money; slippage and full costs included.

## 6. Automation and "trade partner"

- The 24/7 partner is the **TradeX rule engine** (deterministic Python), not an LLM.
- Claude's role: developer and analyst (build, test, review results, improve rules).
- Automation levels, each requiring explicit approval:
  0. Alerts only (start) · 1. One-tap confirm in Telegram · 2. Auto-exit (stops/trailing) · 3. Full auto (only if proven)
- Safety: stop-loss orders resting at broker, kill switch, hard daily/monthly limits, heartbeat alert, full action log.
- Check SEBI retail-algo / Groww requirements (static IP, algo tagging) before Level 1.

## 7. Website

- Private (login), HTTPS, secrets server-side only.
- Pages: Market Overview, Futures/Options Analysis, Options & Context, Signal Explanation, Signal History & Performance, **My Trades** (read-only, PAPER vs REAL).

## 8. Hosting

- Development: Claude Code cloud session or owner's personal laptop.
  - Cloud session needs `groww.in` and `api.groww.in` in allowed domains and Groww credentials as secrets (`GROWW_API_KEY`, `GROWW_API_SECRET`). The Groww key can place orders — owner decides whether to store it in the cloud environment.
- 24/7 running (approved 2026-10-16): **AWS Lightsail Mumbai**, static IP included. Started on the **$7 / 1 GB plan** (~₹785/month incl. GST)
  because the new AWS account was blocked from the $12 / 2 GB plan; move up via snapshot if needed.
  The dashboard is built with webpack (~400 MB) so builds fit next to the running stack, plus 2 GB swap.
  All services, including PostgreSQL and the dashboard, run on that one server with Docker Compose (a managed database
  would cost ~₹1,700/month more). Dashboard is private via Tailscale; public port 80 stays closed.
  The server pulls `main` every 5 minutes, so Claude never needs SSH access. Groww key lives only in the server's `.env`.
  Setup guide: `docs/05_CLOUD_SERVER_SETUP.md`.

## 9. Core metrics (Milestone 2, version M2-1.0, 2026-10-18)

- Computed on the underlying's own VALID, completed daily candles (index value / stock cash price), not on futures.
- EMA 20/50/200, weekly EMA 20, ADX/+DI/−DI 14, RSI 14, ATR 14 (Wilder smoothing), ATR % of price,
  distance from EMA20 in ATRs, and 20-day relative strength vs NIFTY (ratio of 20-day returns; >1 = stronger than NIFTY).
- Warm-up: EMA/ADX/RSI/ATR are hidden (missing) below 250 daily candles, EMA200 below 400, weekly trend below 60 weeks.
- The four direction readings D1–D4 from the strategy spec (§5) are shown as UP / DOWN / NEUTRAL / MISSING.
  They are information only. No LONG/SHORT signal is produced until the rules are backtested.
- Code: `backend/app/services/indicators.py`, `backend/app/services/metrics.py`; served in `/api/market/overview` → `metrics`.

## 10. Data-quality badge rules (2026-10-18)

- The badge answers "can I trust the recent data?". FAIL = the newest candle is invalid or the source failed.
  WARN = a problem in the last 30 days of the batch (`RECENT_DAYS`). Older problems are still logged with counts
  and examples in `data_quality_log`, but give PASS.
- Exact duplicate candles (where two 180-day request windows overlap) are merged and counted, PASS.
  Conflicting duplicates stay FAIL.
- Missing open price is always PASS (counts still logged). Groww sends no 09:15 open for stocks and stock futures:
  the daily candle and the first 1h candle both lack it (checked on the server 2026-10-18). No metric uses the open.

## 11. Backtest of the four readings (2026-10-18)

- Signal study SS-1.0: the readings as specified have no edge. Shorts lost money in both periods,
  and longs were only slightly positive. Shorts are dropped.
- Long-only variants LV-1.0, scored on the development years only. The trailing stop (variant C:
  highest close − 3 ATR, no target, at most 40 days) was the only candidate: 107 trades, +0.23R,
  profit factor 1.45. It was flat in the second half, though, and BHARTIARTL carried most of it.
- RK chose a one-time final test of C on the held-back years. The pass bar was agreed before
  any result was seen: at least 30 trades, expectancy +0.15R or more, profit factor 1.3 or more,
  and no instrument above 50% of total R. If C fails, the rules are not tuned and re-tested on
  the same data.
- **Result (2026-10-18): FAILED.** On validation, from 2025-04-01, C made 52 trades at +0.21R with
  a profit factor of 1.37, but SBIN and AXISBANK made all the profit: together they earned more
  than the total, and the rest lost 0.7R. The instruments that won and lost in development
  swapped places. Price-trend rules on these underlyings stop here.
- Next (RK's choice): study the derivatives data, futures open interest and options. Step 1 is
  `deploy/tradex.sh probe`, which measures how far back Groww keeps that history.

## 12. Open interest study and option-chain collection (2026-10-18)

- The expired monthly futures since 2020 are loaded (`deploy/tradex.sh futures-history`, 80 of 81
  months per underlying). In the OI study OI-1.0 the textbook build-up readings failed: at most a
  faint contrarian effect, about 0.2% over 10 days, whose sign flips between years. Five studies
  now agree that daily data on these 12 instruments has no dependable direction edge.
- RK's choice: collect our own option-chain history. From 15:15 IST each trading day the collector
  stores the full chain of the next 2 expiries for every underlying, exactly as Groww sends it
  (table `option_chain_snapshots`). Field names were checked with `deploy/tradex.sh chain-fields`
  on 2026-10-18: chain `strikes`, `underlying_ltp`; each CE/PE leg `ltp`, `volume`,
  `open_interest`, `trading_symbol`, `greeks` (`delta`, `gamma`, `theta`, `vega`, `rho`, `iv`).
  No bid/ask and no OI-change field. Units (IV in % or decimal, OI in contracts or shares) stay
  `NOT_VERIFIED` until the first stored snapshot is checked. After 6–12 months this supports volatility studies (IV vs
  realised volatility, PCR, OI by strike).

## 13. Hourly chart reading next to the daily history (2026-10-18)

- RK asked for live chart reading together with the history and chose "both together":
  - Each dashboard card shows hourly readings H1 trend, H2 momentum, H3 pullback (version H-1.0)
    from the underlying's completed 1h candles, refreshed after each completed hour. Information
    only, like D1–D4.
  - Study MTF-1.0 (`deploy/tradex.sh hourly-study`, development years only) tests whether timing
    long entries on the hourly chart inside the daily trend beats entering on the daily trend
    alone. Needs hourly history first: `deploy/tradex.sh backfill --timeframes 1h --days-1h 2400`.
- Live option prices and bid/ask at entry are not built; they come after a rule passes its test.
- MTF-1.0 development result (2021-10 to 2025-03): hourly timing adds little. A (daily trend only)
  +0.09R over 1212 trades; only D (hourly breakout) beat A in both halves, +0.14R, PF 1.30, edge
  +0.05R (within noise). B, C, E failed a half. Final test of D (`deploy/tradex.sh hourly-final`),
  bar fixed before any validation result: ≥100 trades, ≥+0.10R, PF ≥1.2, beats A on the same
  period, no instrument >50% of total R. Run once; if it fails, this rule family stops.
- **Final test of D FAILED** (2025-04-01 to 2026-10-16): D 327 trades, −0.07R, PF 0.86; A 482
  trades, −0.03R, PF 0.94. Only the trade count passed; NIFTY −11R, FINNIFTY −9R. Six studies
  now agree: no price-direction edge (daily or hourly) on these 12 instruments. Not re-tuned.

## 14. India VIX study (2026-10-18)

- RK chose to test a volatility edge instead of direction: does India VIX (30-day implied
  volatility of NIFTY options) usually exceed NIFTY's realised volatility over the next 21
  trading days? Study VRP-1.0 (`deploy/tradex.sh vix-study`), development years only, a study
  and not a trade. VIX history is fetched as `NSE-INDIAVIX` (`NOT_VERIFIED` until it loads) and
  stored as an inactive instrument; VIX candles are not spike-checked (real 50% jumps happen).

## 15. One-shot screen of every metric with history (2026-10-18)

- RK asked why metrics were being added one at a time when about 60 were agreed. Sorted the
  catalog: about 22 are rules or controls (stale data, risk limits), not predictors; about 16
  are option-chain metrics with no history yet (collected daily from PR #18); about 14 need a
  source outside Groww (FII/DII, GIFT Nifty, global markets, events), which costs money and
  needs RK's approval; **26 can be tested now**. RK chose "Build full screen".
- Study FS-1.0 (`deploy/tradex.sh screen`): one table per instrument per day with all 26 metrics
  (price, trend, RSI, MACD, ADX, weekly trend, relative strength, correlation, HV, ATR, India
  VIX, basis, carry, OI change, build-up, futures volume, rollover, days to expiry). Targets:
  return over the next 5/10/20 days from D+1's close, and volatility surprise
  (log of next-21-day realised volatility over HV20).
- Scoring: monthly Spearman IC, t across months, Benjamini–Hochberg q < 0.10 over all 104
  tests, same sign in both halves. Metrics built from recent volatility relate to volatility
  surprise by construction (volatility mean-reverts): shown, marked `m`, never survivors.
- At most 3 survivors go forward, combined into one rule whose pass bar is fixed in code before
  a single final test on the held-back data. If none survive, the price/derivatives history on
  these 12 has no usable daily edge, and the next evidence comes from the option chains being
  collected.
- **FS-1.0 result was a scoring bias, not a signal.** RK's first run showed 50 survivors (RSI,
  EMA distance, DI, weekly trend "reversing", t up to −8). The same screen on simulated random
  prices that move together like these 12 gave the same picture (IC about −0.2, t about −9):
  correlating each month's days over time shows false reversal when everything moves together.
  FS-1.1 replaces the scoring before any rule was built on it:
  - per-instrument metrics: instruments ranked against each other on each day (cross-sectional
    IC), each instrument's own average target taken out first (a stock that drifted up all
    period cannot make momentum look predictive), Newey–West t across months;
  - market-wide metrics (India VIX, VIX minus HV20, VIX change, days to expiry): NIFTY, one
    non-overlapping observation per month;
  - pass bar |t| >= 4.0 (cross-section) / 3.0 (market) on top of BH, calibrated on 40 simulated
    random-price worlds so a false survivor appears in about 1 world in 20.
- **FS-1.1 result on the server (2026-10-18):** 1 survivor of 104. No metric predicts price
  direction (VIX vs returns t 1.7; build-up, OI, basis, volume near zero): the seventh study to
  find no daily direction edge on these 12. The survivor: `vix_chg_5d` vs volatility surprise
  (market test, IC +0.41, t 3.3 vs bar 3.0, halves +0.33 / +0.46): after India VIX rises over 5
  days, NIFTY's next month tends to be more volatile than its HV20. A volatility warning, not a
  buy/sell signal; one simulated random market also reached t 3.3, so it is treated as "maybe".
- One-time final test written (`deploy/tradex.sh vix-filter-final`), bar fixed in code before any
  held-back day was looked at: "VIX jumped" = 5-day change above its development 80th
  percentile; held-back NIFTY days need >= 30 jump days, >= 3 separate episodes, next-month
  volatility surprise after jumps above other days by >= 0.10 (log), >= 55% of jump days followed
  by a month above HV20, and a positive month-end rank correlation. If it passes, its only use is
  a NO TRADE filter for option selling. If it fails, it is dropped, not re-tuned.
- **Final test PASSED (2026-10-18), the first rule to pass.** Held back (2025-04-01 to
  2026-09-15, 360 NIFTY days): 64 jump days in 9 episodes; volatility surprise after jumps minus
  other days +0.27 (development +0.12); 58% of jump days followed by a month above HV20
  (development 59%); month-end rank correlation +0.32 over 18 months. Threshold: 5-day VIX rise
  above 7.8%.
- RK chose "Dashboard + Telegram". Rule **VJ-1.0** (`backend/app/services/vix_warning.py`):
  WARNING when India VIX's latest complete close is more than 7.8% above its close 5 trading
  days earlier, which means option selling is NO TRADE. Missing or stale VIX (more than one trading
  day behind) gives NO_DATA, which is also NO TRADE for option selling. The dashboard shows a banner, and the
  collector loads VIX after the close and sends one Telegram alert on the first day of each
  jump. Not a buy/sell signal; no orders. Changing the threshold means a new version and a new
  test.

## 16. Option history from NSE's free F&O file, and the option screen (2026-10-18)

- RK asked for every catalog metric's data. `docs/07_DATA_SOURCES.md` maps them. The server can
  download NSE's archive files (checked with `tradex.sh sources`); NSE's home page answers 403,
  but the archive works. FRED timed out (retry later).
- NSE's daily F&O file is loaded into `fo_daily` from 2020 (`tradex.sh fo-history`, 1,679 days,
  0 failures) and the collector adds each day from 19:00 IST. Kept: every future, and options of
  the nearest 2 expiries plus 2 monthly expiries within ±15% of the price that traded or had OI.
- Option metrics (`tradex.sh option-metrics`) on the nearest monthly expiry with 7+ days left.
  Checks passed on the server: NIFTY ATM IV vs India VIX correlation 0.97 (IV 1.4 points below
  VIX, as expected); OI is in shares in both the old and new file (ratios 0.75–1.18), so OI
  history is comparable across NSE's July 2024 switch.
- Option screen OS-1.0 (`tradex.sh option-screen`): FS-1.1's rules, plus target rv_vs_iv
  (next month's realised volatility vs today's ATM IV). Run on random worlds first: IV-based
  metrics "predicted" both volatility targets on pure noise (|t| 15–30: IV knows today's
  volatility better than HV20, and its measurement noise reverts), so those pairs are marked
  mechanical. Market-wide bar raised from 3.0 to 3.3 (25 market tests here, not 16), giving a
  false survivor in about 1 random world in 20. Bars fixed before the real run.

## 17. Option screen result and FII/DII positioning screen (2026-10-19)

- OS-1.0 on the server (section 3): options were usually overpriced. On every name the next
  month's realised volatility was below ATM IV on 59–74% of development days (median log ratio
  −0.06 to −0.19), but in the worst 5% realised volatility was 1.3–1.7× IV. So any option-selling
  rule must have defined risk. FINNIFTY has only 18 option days.
- RK asked for "whatever it takes" to reach clear enter/exit decisions. Next: a backtest of
  protected monthly option selling on NIFTY (skipping VJ-1.0 warning days), development years
  only, then one final test. Selling changes the strategy spec (TX-POS-1.0 buys debit spreads),
  so it needs RK's approval before any live use.
- PS-1.0: NSE participant-wise OI (FII / DII / Client / Pro long and short contracts) loaded into
  `participant_oi` (`tradex.sh participant-history`, collector adds each day after the F&O
  file). Six market-wide readings (long shares of index futures, options call-vs-put bias),
  tested on NIFTY one day per month with FS-1.1's rules. Bar |t| ≥ 3.3, set on 150 random worlds
  (3.0 let noise through in 8% of worlds, 3.3 in 4%). File column names are NOT_VERIFIED until
  the first server load; a changed header fails the day loudly.
- OS-1.0 survivor: `call_wall_pct` (biggest call-OI strike vs forward) against rv_vs_iv (IC −0.137,
  t −7.5) and vol_surprise (IC −0.114, t −5.6), same sign every year. Possible confound: high-IV
  names have walls further out. VS-1.0 re-scores it after removing ATM IV, IV rank and the last
  20 days' move.
- VS-1.0 (`tradex.sh option-selling`): per underlying and monthly expiry, enter the day after the
  previous monthly expiry, sell a fly (ATM short, 1 SD wings) and a condor (0.5 SD short, 1.5 SD
  wings), all four legs must have traded, hold to expiry, settle on the underlying close.
  Variants A all / B skip VJ-1.0 warning (day before entry) / C B + call wall in top half.
  Costs are estimates (1% index / 3% stock slippage per leg, 0.15% charges, ₹20 per leg).
  Development expiries only; a chosen rule then gets one final test with its bar fixed first.


## 18. Results, and the change of direction: data only (2026-10-19 to 2026-10-28)

- PS-1.0 on the server (1,680 days loaded): no survivors. Best links t 2.2 (Pro futures long
  share → vol_surprise) and 2.0 (Client option bias → vol_surprise), bar 3.3. FII/DII
  positioning is not an entry signal.
- VS-1.0 on the server: no edge worth a final test. Average R per trade after costs: A fly −0.09,
  condor −0.05; B −0.05 / −0.02; C (B + call wall top half) −0.00 / +0.01 (profit factor 1.06).
  Years alternate; FINNIFTY had 79 months skipped (illiquid wings). The held-back period was not
  spent on a break-even rule.
- Summary of all studies: no daily or hourly rule (direction, positioning, option selling) beat
  costs on 2020–2025. Only VJ-1.0 (VIX-jump warning) passed its final test.
- **2026-10-28, RK: rules are out.** No buy/exit rules, rule studies, paper journal or risk/sizing
  rules are built from now on. TradeX focuses on **option data points** (live option chain: OI and
  OI change by strike, PCR, max pain, ATM IV, call/put walls) and **live chart reading** (charts
  that update during the day). Existing study code and the VJ-1.0 banner/alert stay as they are
  (RK chose "keep, stop building"); nothing new is added to them.
- The 14 rule rows were removed from the metric catalog (Risk & Position Sizing, Option Trade
  Management, Portfolio Risk Controls): 67 metrics remain. `docs/06_STRATEGY_SPEC.md` stays as a
  record but is not being built.

## 19. Change of focus: intraday, NIFTY 50 + NIFTY Next 50 (2026-10-29)

- **RK, 2026-10-29:** move to the intraday market, covering every NIFTY 50 and NIFTY Next 50
  stock (100 names), using past data, live chart reading and fundamentals. The tracking list
  gets every intraday data point.
- The catalog now has 36 intraday rows (103 in total) in eight new groups: universe, price levels
  (previous-day levels, gap, opening range, pivots/CPR), trend and momentum (VWAP, 5-min EMA/RSI,
  Supertrend), volume and liquidity (RVOL, delivery %, depth), F&O-stock derivatives (intraday
  OI, PCR, basis), breadth (advance/decline, sectors, VIX), fundamentals (valuation, results,
  corporate actions, pledges) and intraday data quality.
- Constituents are read from NSE's index files, never typed in (they change twice a year).
- Not yet verified: Groww 5-min / 15-min candles (only 1h and 1d are wired), how far back
  intraday history goes, quote depth and circuit fields, and every NSE file named in the new rows.
  These are checked from the server before anything is built on them.
- ~~Still in force from §18 unless RK says otherwise: data only, no buy/exit rules.~~
- **RK, 2026-10-29 (later):** wants morning buy/sell signals for intraday, with a sell notification,
  decided from the data points ("test data one two days ... give me signal to buy or sell in
  morning"). This reopens signals for intraday only. Agreed order: (1) collect the data points;
  (2) test each setup on as much past intraday data as Groww gives, after costs (one or two days
  can't tell an edge from luck); (3) setups that pass go to Telegram marked PAPER for 2-3 weeks;
  (4) real alerts only after RK approves the paper results. Alerts only, never orders (V1 rule).
- First check: `deploy/tradex.sh intraday-probe` (reports/intraday_probe.md): 1/5/15-min candle
  depth and request size, a full day of 5-min bars, quote fields, the two NSE member lists, the
  delivery file, and which members Groww lists and which have futures.
- Server note: 100 stocks polled every 5 minutes is about 200 Groww calls per cycle (quote + 5-min
  candle). That fits Groww's documented 10 calls/second; the 1 GB server's database growth
  (~7,500 5-min candles a day) is small.

## 20. 9/21 EMA crossover on NIFTY options: backtest, then paper (2026-11-01)

- **RK, 2026-11-01:** shared a Google Doc of build prompts ("9/21 EMA Cross Over - Algo Trading"):
  Groww setup, a 9/21 EMA NIFTY options backtester with a Flask trade viewer, and a live/paper
  engine. RK asked for it to be set up inside TradeX.
- Rules and every settled ambiguity: [rules doc](https://claude.ai/artifact/PiDc6FGqdqS6xnc1z6sZq4)
  (fill at the next 1-minute option open, tie is not a cross, roll on expiry day, skip on missing
  data, flat 65 lot, ₹1L capital for drawdown %). Code: `backend/app/ema921/`.
- One clarification made while building: a cross needs the sign of EMA9 − EMA21 to really flip
  from the last non-zero side. "Above, tie, above again" is not a new cross.
- **RK chose (decision card):** backtester + paper trading only. No real orders can be placed;
  any Groww order code needs a separate approval after paper results and a check of SEBI's
  retail-algo rules and Groww's requirements.
- **RK:** test on 2-3 years. Default 2 (`--years 3` if Groww has the history).
- Order of work: (1) `tradex.sh ema-probe` checks Groww's 1-minute depth for spot and expired
  options; (2) `ema-load` fetches only the option contracts a trade could need; (3)
  `ema-backtest` writes the report; then the Flask trade viewer; then the paper engine.
- Trade viewer: Flask (as RK's doc asks), served by waitress as the `ema` service at `http://<server>/ema/`.
  GET only, computes a filter combination on first view and caches it. Chart library: TradingView
  Lightweight Charts 4.2.3 (Apache 2.0), copied into the repo so the page needs no CDN.
- Server probe (2026-11-01): Groww returns 1-minute candles for NIFTY and for expired NIFTY options
  back 3 years, stamped with the start minute (09:15 first), plus an extra 15:30 candle the engine
  ignores. One option day came back twice (fixed: one row per minute is kept). Two probe days could
  not find the ATM strike in Groww's contract list (being checked).
- Paper engine: `ema-paper` service, every minute in market hours. It re-runs the backtest engine
  on today with `as_of` = now, so paper trades follow exactly the backtest rules (a test checks a
  simulated day matches the backtest trade for trade). One Telegram message per signal (all modes),
  marked PAPER, plus an end-of-day trade report (every trade, then totals per mode) as text and as a picture; stale NIFTY data (> 3 minutes) means NO TRADE. Default 5m bars,
  09:30-14:30, all three modes; change with EMA921_PAPER_* in .env, switch off with EMA921_PAPER_OFF=1.
- First backtest (2026-11-01, 2024-11-01 to 2026-10-31, 5,001 trades): all 9 timeframe x mode
  combinations lost after costs (profit factor 0.73-0.93). With zero slippage (charges only), 5m was
  positive (PF 1.06-1.13), 15m about break-even, 30m still negative. 5m breaks even at roughly
  0.35-0.8 point of slippage a side. The third year (Nov 2023 - Oct 2024) is held back, untouched.
- **RK, 2026-11-01:** add a standard trailing stop to the rules. Added: a chandelier stop on NIFTY
  spot, 2 x ATR(14) of the signal bars from the highest high (long) / lowest low (short) since
  entry, never loosened, checked on each closed signal bar. A close beyond it exits at the next
  1-minute option open (TRAIL_STOP); no re-entry until the next cross. Same rule for all three
  modes, on by default in the backtest, viewer and paper engine; `--stop-atr 0` turns it off.
  The multiplier was chosen before seeing results (2 is the middle of the 1.5-3 range commonly used),
  not tuned.
- Not verified (before the probe): Groww's "1minute" interval, 1m candles for expired options, candle stamps being
  start times (the probe checks all three), and every cost rate in `ema921/costs.py`.

## 21. ST-1.0: 09:20 hedged short straddle on NIFTY (2026-11-01)

- **RK, 2026-11-01:** wants strategies that take small profits ("less than 5% is also ok").
  Options offered: 09:20 hedged short straddle, opening-range breakout, cash-and-carry.
  **RK chose (decision card):** the 09:20 hedged short straddle. Backtest only, no orders.
- Why this one: OS-1.0 found NIFTY options usually overpriced (realised below implied on 59-74%
  of days). Caution recorded: VS-1.0 (monthly protected selling, held to expiry) was about
  break-even, so the odds are not high.
- Rules fixed before any result (full text in `backend/app/straddle/__init__.py`): skip VIX-jump
  days (VJ-1.0) and days without the previous VIX close; nearest weekly expiry, roll on expiry
  day; ATM from NIFTY's 09:19 close; sell ATM CE + PE, buy CE +500 and PE -500 at the 09:20
  one-minute open; 30% stop on each sold leg, on the 1-minute high, filled at the stop or the
  gap open; everything else closed at the 15:15 open; 65 a leg; slippage 0.5% + 0.5 point a
  side plus the full cost stack.
- Pass bar (default slippage, 2 development years): net > 0, profit factor >= 1.10, each year
  net > 0. Only then one check on the held-back year (Nov 2023 - Oct 2024): net > 0 and
  profit factor >= 1.05. Nothing re-tuned on the same data.
- Commands: `tradex.sh straddle-load` (four legs a day, reuses the EMA spot and expiry data),
  then `tradex.sh straddle-backtest`.
- **Result (RK's server run, 2026-11-01, 2024-11-01 to 2026-10-31, default slippage): FAIL.**
  379 days traded, 48% winning days, gross +₹83k, friction ₹2.70L (about ₹710 a day: 8 orders,
  slippage on 4 legs), net −₹1.86L, profit factor 0.65; year 1 −₹1.15L, year 2 −₹72k. 47% of sold
  legs hit their stop; both legs stopped on 54 days. Skipped: 89 VIX-jump days, 19 without the
  previous VIX close, 18 without NIFTY data. Charges alone (about ₹230 a day) would roughly cancel
  the gross edge, so even perfect fills would be about break-even. Per the rules: no held-back
  test, no re-tuning. NIFTY option selling joins the families with no edge after costs.


## 22. ORB-1.0 and ET-1.0 on NIFTY and BANKNIFTY options (2026-11-02)

- **RK, 2026-11-02:** asked for strategies with a better profit factor than 9/21 EMA. Ranked
  comparison: https://claude.ai/artifact/JsR8tZYtiodBDb4umWbzGd (the lever is fewer trades and
  catching trend days, since friction of ₹220-350 a trade ate the EMA edge). **RK chose (decision
  card): backtest both** the opening-range breakout and the 9/21 EMA with a trend-day filter.
  **RK, same day:** do the same testing on BANKNIFTY.
- Rules fixed before any result (full text in `backend/app/orb/__init__.py` and
  `backend/app/ema921/trend.py`):
  - ORB-1.0: range = 1m high/low 09:15-09:29; first 5m close beyond it, 09:35-13:00; one trade a
    day; stop = a 5m close back beyond the other side (RANGE_STOP); square-off 15:00.
  - ET-1.0: 5m 9/21 cross, entries 09:30-14:00 only if the last closed 15m bar's EMA9/21 agrees
    and the close is on the same side of the day's 09:15 open (stands in for VWAP: an index has no
    volume). Opposite crosses always exit. No trailing stop.
  - Both: same three modes, fills, slippage (0.5% + 0.5 pt) and cost stack as 9/21 EMA.
- BANKNIFTY: lot 30, strikes every 100 (NOT_VERIFIED for all past dates), monthly expiries only;
  spot symbol NSE-BANKNIFTY. Groww's 1-minute depth for expired BANKNIFTY options is
  NOT_VERIFIED: the load reports empty contract-days.
- Pass bar (per underlying, strategy and mode, 2 development years, default slippage): net > 0,
  profit factor >= 1.10, each year net > 0, max drawdown < 50% of ₹1L. 12 rows are scored, so only
  the single best passing row gets one run on the held-back year (Nov 2023 - Oct 2024): net > 0,
  profit factor >= 1.05. Nothing is re-tuned.
- Commands: `ema-load --underlying BANKNIFTY` (EMA legs), `orb-load` and
  `orb-load --underlying BANKNIFTY` (ORB legs), then `orb-et-backtest`
  (reports/orb_et_backtest.md, orb_et_trades.csv). Backtest only; nothing here is wired into the
  paper engine or places orders.
- Result (RK's server run, 2026-11-03; Nov 2024 - Oct 2026, default slippage, 4,110 trades):
  **all 12 rows FAIL**, so no held-back-year run and no re-tune.
  - NIFTY ORB: net -₹1.69L to -₹3.13L, PF 0.75-0.78, max DD 174-309%.
  - NIFTY ET: gross positive in all modes (+₹30k to +₹66k) but net: ATM sell +₹8.6k (PF 1.05;
    year 1 +₹23.8k, year 2 -₹15.2k), ATM buy -₹19.6k (0.91), Deep ITM buy -₹11.1k (0.96).
  - BANKNIFTY ORB: net -₹2.17L to -₹4.33L, PF 0.64-0.75. BANKNIFTY ET: net -₹0.89L to -₹1.47L,
    PF 0.52-0.61. Both lose even before costs.
  - 2026-11-03 RK: auto algo trading plan put ON HOLD (no strategy has passed).

## 23. Three strategies live as numbered PAPER signals (2026-11-03)

- **RK, 2026-11-03:** run several strategies live on the server, not one, with Telegram alerts
  giving a trade number, the strategy name and everything needed to execute, and later an exit
  message by the same trade number.
- Only four strategies exist and none has passed a backtest (9/21 EMA and ST-1.0 failed; ORB-1.0
  and ET-1.0 are waiting for RK's server run), so the three are EMA-921, ORB-1.0 and ET-1.0.
- **RK chose (decision card): PAPER now, real after pass.** Messages stay marked PAPER. A strategy
  switches to real alerts only after it passes its backtest and RK approves. Never orders.
- Built: `backend/app/live/` + `signals` compose service (`scripts/signals_paper.py`). Each
  strategy re-runs its own backtest engine every minute (`as_of`), NIFTY weekly options, one mode
  for all (ATM buy, 1 lot of 65; `SIGNALS_MODE` in .env). Table `signal_trades`; its id is the
  trade number, counted across strategies and days. Entry message: trade #, strategy, contract,
  quantity, 1-minute open price and price after slippage, premium, exit plan (ORB: the range
  level; EMA-921: starting trailing-stop level). Exit message: same #, reason, prices, P&L.
  15:05 day report by trade number. Stale NIFTY data (> 3 min) = NO TRADE.
- The older `ema-paper` service (all three modes of 9/21 EMA) keeps running; switch it off with
  EMA921_PAPER_OFF=1 if the EMA-921 messages feel doubled.
