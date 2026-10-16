# TradeX

Private trading intelligence system for NIFTY, BANKNIFTY and selected F&O stocks.
It collects market data, validates it, calculates technical, open-interest and options metrics,
and applies deterministic, backtested rules to produce **LONG BIAS / SHORT BIAS / NO TRADE** with Telegram alerts.

> Analysis only — no order execution in Version 1.

## Documentation

| File | Contents |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Start here (for Claude and humans) |
| [`docs/00_MASTER_PROJECT_BRIEF.md`](docs/00_MASTER_PROJECT_BRIEF.md) | Original master brief |
| [`docs/01_DECISIONS_AND_CONTEXT.md`](docs/01_DECISIONS_AND_CONTEXT.md) | Decisions made since the brief |
| [`docs/Futures_Tracking_Sheet.xlsx`](docs/Futures_Tracking_Sheet.xlsx) | Metric catalog |
| [`docs/02_SETUP_WINDOWS.md`](docs/02_SETUP_WINDOWS.md) | **How to run it on Windows** |
| [`docs/03_PROVIDER_CAPABILITY_MATRIX.md`](docs/03_PROVIDER_CAPABILITY_MATRIX.md) | What Groww provides (verified vs assumed) |
| [`docs/04_LIVE_ALERTS_ROADMAP.md`](docs/04_LIVE_ALERTS_ROADMAP.md) | Path from here to live Telegram entry/exit alerts |

## How it fits together

```text
Groww API  or  SAMPLE generator      backend/app/providers/
        |
        v
Data-quality checks                   backend/app/services/data_quality.py
        |
        v
PostgreSQL (idempotent upserts)       backend/app/services/ingestion.py, alembic/
        |
        v
Read-only FastAPI                     backend/app/main.py  ->  http://localhost:8000/docs
        |
        v
Next.js dashboard                     frontend/            ->  http://localhost:3000

Collector (always-on, market hours)   scripts/run_collector.py  ->  Telegram heartbeats
```

## Quick start

See [`docs/02_SETUP_WINDOWS.md`](docs/02_SETUP_WINDOWS.md). In short:

```powershell
copy .env.example .env
cd backend; python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt; alembic upgrade head; python -m pytest; cd ..
python scripts\backfill_candles.py
# window 1:  cd backend; uvicorn app.main:app --reload
# window 2:  cd frontend; npm install; npm run dev
```

## Status

Milestone 1 (data pipeline + dashboard) built and tested with clearly labelled **sample data**.
Next: Milestone 0 verification with real Groww credentials, then core metrics.
