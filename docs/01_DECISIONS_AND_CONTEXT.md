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
- 24/7 running: small cloud server + managed PostgreSQL + Vercel for the frontend. Compare prices and get approval first.
