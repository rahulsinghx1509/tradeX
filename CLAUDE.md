# TradeX — Claude Start Here

TradeX is a private trading intelligence system for the Indian derivatives market.
It collects market data, validates it, calculates metrics, and applies deterministic,
testable rules to produce **LONG BIAS / SHORT BIAS / NO TRADE**, with Telegram alerts.

Read these before doing any work:

1. `docs/00_MASTER_PROJECT_BRIEF.md` — the owner's original master brief (scope, architecture, schema, rules).
2. `docs/01_DECISIONS_AND_CONTEXT.md` — decisions made in planning discussions **after** the brief. Where it differs from the brief, the decisions file wins.
3. `docs/Futures_Tracking_Sheet.xlsx` — the metric catalog (Metric Dictionary sheet: 103 data metrics, 36 of them intraday (added 2026-10-29); the 14 rule rows were removed 2026-10-28).

## Owner

- Data analyst, strong in SQL, beginner in Python (wants to learn while building).
- Explain architecture and code clearly; prefer simple, maintainable solutions.
- Build in small milestones; run tests; report what works, what is untested, what is blocked.

## Non-negotiable rules

- Never fabricate market data, endpoints, fields or provider permissions. Mark unverified things as `NOT_VERIFIED`.
- Never ask for API keys, passwords or TOTP in chat. Secrets live in `.env` (gitignored) or the environment's secret settings.
- No order placement in Version 1. Automation levels (alerts → one-tap → auto-exit → full auto) each need explicit owner approval.
- No LLM as the trade decision-maker. Signals come from versioned, deterministic, backtested rules.
- Missing values stay missing (never zero). Stale or invalid data ⇒ `NO TRADE`.
- Ask before destructive actions or anything that costs money.

## Current status

- ✅ Repo created; metric catalog merged (PR #1); handoff docs merged (PR #2).
- ✅ Milestone 1: pipeline (providers → data-quality → PostgreSQL → FastAPI → Next.js), Telegram sender,
  market-hours collector. Tested with SAMPLE data only. Groww adapter written from docs, `NOT_VERIFIED`.
- ✅ Hosting chosen: AWS Lightsail Mumbai ($7 / 1 GB to start), everything in Docker Compose (`deploy/`), guide in `docs/05_CLOUD_SERVER_SETUP.md`.
- ✅ Milestone 2 core metrics (EMA, ADX, RSI, ATR, relative strength, D1–D4 readings) on each dashboard card. Not signals.
- ✅ Studies (docs/01 §11–15): no daily or hourly price-direction edge found on the 12 instruments.
- ✅ VJ-1.0 VIX-jump volatility warning passed its one-time final test: dashboard banner + Telegram
  alert, option selling NO TRADE on jump days. The only tested rule; not a buy/sell signal.
- ✅ Every catalog metric mapped to a data source (`docs/07_DATA_SOURCES.md`); free sources need checking from the server (`tradex.sh sources`).
- ✅ NSE's free daily F&O file loads into `fo_daily` (`tradex.sh fo-history` from 2020; collector adds each day from 19:00 IST).
- ✅ Option metrics (ATM IV, skew, PCR, max pain, OI walls) rebuilt from `fo_daily` (`tradex.sh option-metrics`, checked against India VIX).
- ✅ OS-1.0 option screen run: options were usually overpriced (docs/01 §17).
- ✅ FII/DII positioning file loads into `participant_oi` (`tradex.sh participant-history`); screen PS-1.0.
- ✅ PS-1.0 and VS-1.0 run: no edge. No daily rule beat costs (docs/01 §18).
- 🔀 2026-10-28 RK: **rules are out**. No buy/exit rules, rule studies, paper journal or sizing rules.
  Existing study code and VJ-1.0 stay as they are; nothing new is built on them.
- 🔀 2026-10-29 RK: focus moves to **intraday**, all NIFTY 50 + NIFTY Next 50 stocks (docs/01 §19);
  36 intraday data points added to the catalog.
- 🔀 2026-10-29 RK (later): intraday signals are back in scope: data points first, then setups tested on
  past intraday data, PAPER Telegram signals, real alerts only after RK approves (docs/01 §19). Never orders.
- 🔀 2026-11-01 RK: 9/21 EMA crossover on NIFTY weekly options (docs/01 §20). Backtest engine, Groww
  1-minute loader, Flask trade viewer (`/ema/` on the server) and live PAPER engine (`ema-paper` service,
  PAPER Telegram messages) in `backend/app/ema921/`. No real orders.
  First backtest: no edge after costs; 2026-11-01 RK added a 2 x ATR(14) chandelier trailing stop (docs/01 §20).
- 🔀 2026-11-01 RK: next strategy ST-1.0, the 09:20 hedged short straddle on NIFTY (docs/01 §21),
  `backend/app/straddle/`. Result: FAIL (net −₹1.86L over 2 years, PF 0.65); no held-back test.
- 🔀 2026-11-02 RK: backtest ORB-1.0 (opening-range breakout) and ET-1.0 (9/21 EMA + 15m trend
  filter) on NIFTY and BANKNIFTY options, pass bar fixed first (docs/01 §22). `backend/app/orb/`,
  `ema921/trend.py`, `tradex.sh orb-load` / `orb-et-backtest`. Result 2026-11-03: all 12 rows FAIL (best NIFTY ET ATM sell, PF 1.05). Auto-trading on hold.
- 🔀 2026-11-03 RK: EMA-921, ORB-1.0 and ET-1.0 run live as numbered PAPER signals (`backend/app/live/`, trade numbers `YYYYMMDD-NN` since 2026-11-05,
  `signals` service, table `signal_trades`; docs/01 §23). Real alerts only after a strategy passes and RK approves.
- 🔀 2026-11-03 RK: intraday STOCKS (NIFTY 50 + Next 50), docs/01 §24: `stock-load` then `stock-backtest`
  (IS-1.0: ORB / VWAP pullback / gap-and-go, long + short). Result 2026-11-03: all 6 rows FAIL; no scanner built.
- 🔀 2026-11-03 RK: volume in the live analysis (docs/01 §25, VF-1.0): NIFTY near-month futures 1-min volume
  (`tradex.sh fut-load`), RVOL line in every PAPER entry alert (info only), `volume-backtest` of a volume
  entry filter on EMA-921/ORB/ET. Result pending; filter goes live only if it passes and RK approves.
- 🔀 2026-11-05 RK: SP-1.0 spreads (ET credit spreads, range-day iron condor, ORB+volume debit spreads)
  and TP-1.0 trend-pullback option buying with ₹500/₹1k/₹2.5k limits (docs/01 §26), `backend/app/spreads/`,
  `backend/app/pullback/`, `tradex.sh spread-load` / `spread-backtest`. Result pending.
- 👉 Also pending: RK runs `tradex.sh intraday-probe` (Groww 1/5/15-min depth, quote fields, NSE member lists),
  then collect the 100 stocks and show live intraday charts.

## Code map

- `backend/app/core/market_time.py` – IST sessions, holidays, complete-candle rules (UTC storage).
- `backend/app/providers/` – adapter interface, `sample.py` (synthetic, `is_sample=TRUE`), `groww.py`.
- `backend/app/services/` – `ingestion.py` (upserts), `data_quality.py`, `freshness.py`, `collector.py`, `nse_bhavcopy.py` (NSE F&O file).
- `backend/app/api/routes/` – read-only GET endpoints only. A test fails if a non-GET route appears.
- `backend/alembic/versions/` – schema migrations. Change models → `alembic revision --autogenerate`.
- `backend/app/straddle/` – ST-1.0 09:20 hedged short straddle: rules in `__init__.py`, engine, loader (reuses ema921 tables), report.
- `backend/app/live/` – multi-strategy PAPER signal engine: numbered entry/exit Telegram messages (`signals` service).
- `backend/app/orb/` – ORB-1.0 opening-range breakout (rules in `__init__.py`), reuses the ema921 engine's fills/costs.
- `backend/app/ema921/` – 9/21 EMA options backtest (`trend.py` = ET-1.0 filter; `store.UNDERLYINGS` = NIFTY/BANKNIFTY lot and strike step): bars/crosses, costs, engine, metrics, 1m store, Groww loader, probe,
  `web.py` + `static/` Flask trade viewer (GET only; `ema` compose service behind Caddy at `/ema/`).
  `paper.py` re-runs the same engine each minute (`as_of`) and records `paper_trades`; never orders.
  `report_card.py` draws the 15:05 day report as a PNG (Pillow) for Telegram.
- `scripts/` – backfill, collector, Telegram helpers, Groww capability check.
- `deploy/` – server Docker Compose (db, migrate, api, collector, dashboard, caddy), `tradex.sh` everyday commands,
  setup, nightly backup and 5-minute auto-update from `main`. Merging to `main` deploys to the server.
- `backend/app/spreads/` – SP-1.0 credit/debit spreads (extra legs via `ema921.engine.EXTRA_LEGS`, paired by
  signal time) and the range-day iron condor (straddle engine with `short_offset`); `report.py` scores SP + TP rows.
- `backend/app/pullback/` – TP-1.0 trend-pullback option buying (futures VWAP, risk limits); loader re-runs until no leg is missing.
- `backend/app/volume/` – VF-1.0: near-month futures 1m loader (`futures.py`), RVOL + entry filter (`rvol.py`); engines take `entry_filter`.
- `backend/app/stocks/` – intraday stocks (NIFTY 50 + Next 50): 5-minute loader (`tradex.sh stock-load`), IS-1.0 rules (`rules.py`), engine, `stock-backtest`.
- Tests: `cd backend && python -m pytest` (SQLite); set `TEST_DATABASE_URL` to run on PostgreSQL.
