# TradeX — Claude Start Here

TradeX is a private trading intelligence system for the Indian derivatives market.
It collects market data, validates it, calculates metrics, and applies deterministic,
testable rules to produce **LONG BIAS / SHORT BIAS / NO TRADE**, with Telegram alerts.

Read these before doing any work:

1. `docs/00_MASTER_PROJECT_BRIEF.md` — the owner's original master brief (scope, architecture, schema, rules).
2. `docs/01_DECISIONS_AND_CONTEXT.md` — decisions made in planning discussions **after** the brief. Where it differs from the brief, the decisions file wins.
3. `docs/Futures_Tracking_Sheet.xlsx` — the metric catalog (Metric Dictionary sheet: 47 original metrics + 35 added for options, risk and data quality).

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
- 👉 Next: RK runs `tradex.sh option-selling` (VS-1.0, development years); one final test of the chosen rule.

## Code map

- `backend/app/core/market_time.py` – IST sessions, holidays, complete-candle rules (UTC storage).
- `backend/app/providers/` – adapter interface, `sample.py` (synthetic, `is_sample=TRUE`), `groww.py`.
- `backend/app/services/` – `ingestion.py` (upserts), `data_quality.py`, `freshness.py`, `collector.py`, `nse_bhavcopy.py` (NSE F&O file).
- `backend/app/api/routes/` – read-only GET endpoints only. A test fails if a non-GET route appears.
- `backend/alembic/versions/` – schema migrations. Change models → `alembic revision --autogenerate`.
- `scripts/` – backfill, collector, Telegram helpers, Groww capability check.
- `deploy/` – server Docker Compose (db, migrate, api, collector, dashboard, caddy), `tradex.sh` everyday commands,
  setup, nightly backup and 5-minute auto-update from `main`. Merging to `main` deploys to the server.
- Tests: `cd backend && python -m pytest` (SQLite); set `TEST_DATABASE_URL` to run on PostgreSQL.
