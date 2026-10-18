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
