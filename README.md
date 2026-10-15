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

## Status

Planning complete. Next: Milestone 1 (data → PostgreSQL → FastAPI → dashboard).
