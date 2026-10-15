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

- ✅ Repo created; metric catalog merged (PR #1).
- 👉 Next: foundation docs + Milestone 1 (data → PostgreSQL → FastAPI → Next.js dashboard), then Milestone 0 verification with the owner's real Groww API key.
