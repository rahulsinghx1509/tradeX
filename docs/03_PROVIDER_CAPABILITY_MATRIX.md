# Provider capability matrix (Groww Trade API)

Status key: `SUPPORTED` · `UNSUPPORTED` · `NOT_VERIFIED` · `REQUIRES_PAID_PLAN` · `SOURCE_UNAVAILABLE`

"Documented" means Groww's official SDK documentation describes it
(https://groww.in/trade-api/docs/python-sdk, read 2026-10-16, SDK `growwapi` 1.5.0).
The first Milestone 0 run on the owner's account (2026-10-17 00:48 IST, market closed, from the
Lightsail server) updated the rows marked *(M0)*. The other rows failed for reasons on our side
(instrument file permission, rate limit) and are re-checked after the fix.

| Capability | Status | What the docs say | Used by TradeX in |
|---|---|---|---|
| NIFTY index data | SUPPORTED *(M0)* | `groww_symbol` "NSE-NIFTY" (CASH segment) in historical candles | candles, overview |
| BANKNIFTY index data | SUPPORTED *(M0)* | Not shown in docs; "NSE-BANKNIFTY" works | candles, overview |
| Futures identifiers + expiry | NOT_VERIFIED | Instrument CSV with `groww_symbol`, `trading_symbol`, `expiry_date`, `lot_size`, `underlying_symbol`, `instrument_type` | `sync_instruments` |
| Historical OHLC candles | SUPPORTED *(M0)* | `get_historical_candles`; Jan 2020 daily candles returned. Index candles have no volume | backfill |
| Candle intervals | NOT_VERIFIED | 1/2/3/5/10/15/30 min, 1h, 4h, 1 day, 1 week, 1 month | 1d + 1h used |
| Per-request window | NOT_VERIFIED | 1–5 min: 30 days · 10–30 min: 90 days · 1h–1 month: 180 days | adapter splits requests |
| Futures volume | NOT_VERIFIED | Candles include volume for equities and FNO | DQ check |
| Futures open interest | NOT_VERIFIED | 7th candle value, FNO only | chart, overview |
| Expired contracts history | NOT_VERIFIED | `get_expiries` / `get_contracts` accept past years; not stated explicitly | **decides options backtesting** |
| Live quote | SUPPORTED for the index *(M0)* | `get_quote`: last_price, ohlc, depth, volume, open_interest, oi_day_change, implied_volatility, last_trade_time | snapshots |
| LTP / OHLC (batch) | NOT_VERIFIED | `get_ltp`, `get_ohlc`: up to 50 instruments per call | not used yet |
| Streaming feed | NOT_VERIFIED | Feed API, up to 1000 subscriptions | later (live alerts) |
| Option chain + Greeks | NOT_VERIFIED | `get_option_chain`: per strike CE/PE ltp, OI, volume, greeks (delta, gamma, theta, vega, rho, iv) | Milestone 2/3 |
| India VIX | SUPPORTED *(M0)* | Not documented; `get_quote` with "INDIAVIX" (CASH) works | Milestone 2 |
| Historical IV | UNSUPPORTED (per handoff) | Not provided; store ATM IV daily ourselves | Milestone 2 |
| Rate limits | NOT_VERIFIED | Live data 10/s, 300/min · Non-trading 20/s, 500/min · token 150/day | adapter waits 0.15 s between calls and retries rate-limit refusals after 2, 4, 8, 16 s. Groww resets logins daily at 06:00 IST; the adapter logs in again |
| Storage / display rights | NOT_VERIFIED | Not checked | review terms before any public use |

## Assumptions in the adapter that the verify script checks

1. Candle timestamps with no timezone are IST.
2. ~~1-hour candles start at 09:15, 10:15, …~~ Wrong: Groww stamps them on the clock (first one 09:00). TradeX now uses 09:15–10:00, 10:00–11:00, … 15:00–15:30 and pins Groww's 09:00 candle to 09:15.
3. Daily candles are pinned to 09:15 IST of their trading date, whatever time Groww stamps them with.
4. Futures rows in the instrument CSV have `instrument_type = "FUT"`.

## Network access needed

The machine running TradeX must reach `api.groww.in` (API) and
`growwapi-assets.groww.in` (instrument CSV). In a Claude Code cloud environment,
both must be in the allowed domains, plus `api.telegram.org` for alerts.
