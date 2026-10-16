# From Milestone 1 to live entry / exit alerts

Goal: during market hours (09:15–15:30 IST) TradeX checks NIFTY and BANKNIFTY every few
minutes and sends Telegram messages like *"ENTER bull call spread 57000/57800, stop X,
target Y"*, and exit alerts when a stop, target, trailing stop or time stop is hit.

## What already exists after Milestone 1

| Piece | File | Status |
|---|---|---|
| Market calendar (sessions, holidays, complete candles) | `backend/app/core/market_time.py` | done, tested |
| Provider adapter (Groww + sample) | `backend/app/providers/` | sample tested; Groww written from docs, NOT_VERIFIED |
| Idempotent storage + data-quality checks | `backend/app/services/ingestion.py`, `data_quality.py` | done, tested |
| Freshness (stale data ⇒ NO TRADE) | `backend/app/services/freshness.py` | done, tested |
| Telegram sender | `backend/app/alerts/telegram.py` | done, tested with a fake Telegram |
| Always-on collector loop | `backend/app/services/collector.py`, `scripts/run_collector.py` | done, tested; sends heartbeats only |
| Dashboard | `frontend/` | done |

## What comes next, in order

1. **Milestone 0 – verify Groww** with your real key (`scripts/verify_provider_capabilities.py`).
2. **Core metrics** – EMA 20/50/200, RSI, ADX, ATR, OI change, price/OI buildup, IV vs HV,
   expected move. Each with a definition, unit tests and missing-data behaviour.
3. **Strategy spec** – written rules for entry, strike/expiry choice (≥15–20 DTE), stop,
   target (≥2R), trailing stop on the underlying, time stop, position size (1–3% risk),
   portfolio limits (max 2 trades, monthly loss limit). Versioned.
4. **Backtest** – with costs and slippage, no look-ahead, walk-forward validation.
5. **Rule engine in the collector** – after each cycle: if data is fresh and valid, run the
   rules; send ENTER / EXIT alerts; log every alert. Stale or invalid data ⇒ NO TRADE, and
   you get a "data problem" alert instead.
6. **Paper trading for 1–2 months** – alerts are recorded as paper trades with P&L after costs.
7. **Real money, small size** – only after you approve, based on the paper results.

## The always-on server (needs your approval before anything is bought)

The collector must run all trading day, so it needs a small machine that is always on.
Requirements: 1 vCPU, 1–2 GB RAM, PostgreSQL (same box or managed), outbound access to
`api.groww.in`, `growwapi-assets.groww.in`, `api.telegram.org`; Indian region preferred
(lower latency; a static IP may be required later for SEBI retail-algo rules).
Prices will be compared and brought to you for approval. Nothing is deployed until then.

Alert automation levels stay as agreed: 0 alerts only → 1 one-tap confirm → 2 auto-exit →
3 full auto, each needing your explicit approval. Version 1 places no orders.
