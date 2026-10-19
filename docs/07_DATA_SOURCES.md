# 07 — Data sources for every catalog metric

Written 2026-10-18. Maps every metric in `Futures_Tracking_Sheet.xlsx` to where its data comes from.
Anything marked NOT_VERIFIED has not been downloaded from our server yet; `deploy/tradex.sh sources`
checks the free ones (report in `reports/data_sources.md`).

## 1. Summary

| Group | Metrics | Data status |
|---|---|---|
| A. Have it now (Groww candles, futures OI, India VIX) | 26 | Collected and tested in the screen (docs/01 §15) |
| B. Rules and controls (risk, sizing, trade management, data quality) | ~22 | Need no outside data; computed from our own prices, positions and settings |
| C. Option-chain metrics (IV, Greeks, PCR, max pain, strike OI, skew) | ~16 | Groww chain collected daily from 2026-10-19 (forward only). **Free NSE F&O bhavcopy may give years of history: NOT_VERIFIED** |
| D. Outside data (FII/DII, ban list, global, events) | ~14 | Free sources found; none downloaded yet (NOT_VERIFIED) |
| E. Paid only | 2 | Bid-ask spread history, order-book depth. Not needed now; collect our own snapshots forward |

## 2. Metrics needing data we don't have yet

| Metric (catalog) | Free source found | History | Status |
|---|---|---|---|
| IV, IV Percentile/Rank, IV History, IV/HV, IV Skew, Expected Move, Event IV Crush | NSE F&O bhavcopy: option close/settle per strike + underlying price (new UDiFF format from Jul 2024); IV computed by us (Black-76) | Old format back to ~2000 (no underlying price, join index close) | NOT_VERIFIED |
| PCR, Strike OI, Strike OI Change, Max Pain, OI Concentration | NSE F&O bhavcopy (OI and change in OI per strike) | Same | NOT_VERIFIED |
| Delta, Gamma, Theta, Vega | Computed from IV above (Groww chain also gives Greeks forward) | Same | NOT_VERIFIED |
| FII/DII Futures Positioning | NSE participant-wise OI and volume (Client / DII / FII / Pro; long and short) | Depth NOT_VERIFIED | NOT_VERIFIED |
| FII/DII cash flows | NSE FII/DII page (provisional), NSDL FPI daily (confirmed, T+1) | NSE page shows latest day only | NOT_VERIFIED |
| F&O Ban List | NSE `fo_secban` daily file + MWPL usage file (early warning before a ban) | Depth NOT_VERIFIED | NOT_VERIFIED |
| USD/INR, Brent, US 10Y, Nasdaq, Nikkei, dollar index | FRED (St. Louis Fed) CSV | Decades; 1–4 days behind | NOT_VERIFIED from server |
| US indices (S&P 500, Dow) | FRED, last 10 years only; S&P limits reuse | 10 years | NOT_VERIFIED |
| GIFT Nifty | No free history found (was SGX Nifty before Jul 2023) | — | No free source |
| Macro Events (RBI MPC, FOMC, CPI, Budget) | Official calendars (RBI, Federal Reserve, MoSPI); kept by hand in a small file | Published a year ahead | Free, manual |
| Earnings Date, Corporate Actions | NSE board-meeting / corporate-action pages | NOT_VERIFIED | NOT_VERIFIED |
| Bid-Ask Spread % | None free (NSE sells order data). Groww chain has no bid/ask | — | Paid only |
| Rollover Cost, Fair Value, Mispricing % | Need a risk-free rate (T-bill / MIBOR); source not checked | — | NOT_VERIFIED |

## 3. Risks found

- **NSE may block cloud servers.** Forum reports say NSE blocks some AWS address ranges; Lightsail is AWS.
  If `tradex.sh sources` shows BLOCKED, fallback is downloading files from a home PC (no paid service needed).
- **NSE terms of use** forbid "systematic or automated data collection" but allow downloading files
  offered for download. Private use, a few files a day at low speed, is a grey area; we keep it gentle
  (1 request per second, end-of-day only).
- **Yahoo / Stooq** are unofficial or now need keys; not used.

## 4. Build order (free items only)

1. ✅ `tradex.sh sources` run on the server 2026-10-18: NSE archives download (both F&O file formats,
   participant OI/volume, ban list back to 2010, MWPL back to 2015). FRED timed out (retry later).
2. ✅ F&O file loader (`tradex.sh fo-history` for 2020→today; the collector loads each new day from
   19:00 IST) into table `fo_daily`. Next: compute IV, PCR, max pain and strike OI history from it and
   test the option-chain metrics now instead of waiting 6–12 months.
3. ✅ Participant OI (FII/DII positioning): `tradex.sh participant-history` (2020→today) and a daily
   collector step into table `participant_oi`; screened as PS-1.0. Ban list + MWPL still to do
   (none of our 12 names is a usual ban candidate, so they matter only as a safety gate).
4. FRED global series (daily, end of day).
5. Event calendar file (RBI, FOMC, CPI, Budget, expiry days) kept by hand.

Nothing in this list costs money.
