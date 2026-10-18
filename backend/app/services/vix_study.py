"""India VIX versus NIFTY's realised volatility: were option premiums usually too high?

India VIX is NSE's index of the volatility that NIFTY option prices imply for the next 30
calendar days, as an annualised percentage. If option prices are usually "too high", VIX is
usually above the volatility NIFTY then actually delivers, and option sellers earn that gap on
average (the "volatility risk premium"). The price is the occasional month when the market moves
far more than VIX implied, which can wipe out many months of gains.

For every trading day D (development period only, as in every study):
- implied = VIX close on D;
- realised = standard deviation of NIFTY's daily log returns over the next 21 trading days
  (about 30 calendar days, D+1 … D+21 closes), annualised (× √252) and in %;
- gap = implied − realised (volatility points). Positive = options were priced for bigger moves
  than happened;
- seller share = (implied² − realised²) / implied²: roughly the share of a 30-day variance
  premium a seller would have kept. +0.3 = kept 30%; −1.0 = lost the premium again.

This is a study of volatility, not a trade: no option prices, strikes, margins, costs or
slippage. Option selling has large tail risk; nothing here places or suggests orders.
Consecutive days share most of their 21-day window, so the real sample is far smaller than the
day count (about one independent window per month).

Fetching India VIX history uses Groww's candle call with the symbol NSE-INDIAVIX, following the
"NSE-<symbol>" pattern Milestone 0 confirmed for NIFTY and BANKNIFTY: NOT_VERIFIED until the
loader reports candles. VIX is stored as an inactive instrument, so the collector and dashboard
ignore it.
"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.core.market_time import IST
from app.providers.base import InstrumentInfo, MarketDataProvider
from app.services import signal_study as ss
from app.services.data_quality import VIX_SYMBOL
from app.services.futures_history import _upsert_inactive
from app.services.ingestion import IngestResult, ingest_candles

VIX_STUDY_VERSION = "VRP-1.0"
VALIDATION_FROM = date(2025, 4, 1)  # same held-back period as every earlier study
WINDOW = 21  # trading days in about 30 calendar days, VIX's horizon
FIRST_DAY = date(2020, 1, 1)
LEVELS = [(0, 12), (12, 15), (15, 20), (20, 25), (25, 1000)]
VIX_INFO = InstrumentInfo(symbol=VIX_SYMBOL, instrument_type="INDEX", exchange="NSE",
                          segment="CASH", provider_symbol=f"NSE-{VIX_SYMBOL}",  # NOT_VERIFIED
                          trading_symbol=VIX_SYMBOL)


def load_vix(session: Session, provider: MarketDataProvider, now: datetime) -> IngestResult:
    """Fetch India VIX daily candles since FIRST_DAY (safe to re-run: rows are upserted)."""
    inst = _upsert_inactive(session, provider, VIX_INFO)
    start = datetime.combine(FIRST_DAY, datetime.min.time(), IST)
    return ingest_candles(session, provider, inst, "1d", start, now, now)


def daily_table(nifty: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """Implied (VIX), next-21-day realised volatility of NIFTY, the gap and the seller share."""
    df = pd.concat([nifty["close"].rename("nifty"), vix["close"].rename("implied")], axis=1,
                   join="inner").sort_index()
    ret = np.log(df["nifty"] / df["nifty"].shift(1))
    # Realised over D+1 … D+WINDOW: the rolling std ending WINDOW days later, shifted back.
    fwd = ret.rolling(WINDOW, min_periods=WINDOW).std(ddof=1).shift(-WINDOW)
    df["realised"] = fwd * np.sqrt(252) * 100
    df["gap"] = df["implied"] - df["realised"]
    df["seller_share"] = (df["implied"] ** 2 - df["realised"] ** 2) / df["implied"] ** 2
    return df.dropna(subset=["implied", "realised"])


def run(nifty: pd.DataFrame, vix: pd.DataFrame) -> pd.DataFrame:
    """Development days only, and only those whose 21-day window ends before validation."""
    df = daily_table(nifty, vix)
    df = df[df.index < pd.Timestamp(VALIDATION_FROM)]
    # The last WINDOW days' realised windows reach into validation: drop them (no leak).
    return df.iloc[:-WINDOW] if len(df) > WINDOW else df.iloc[0:0]


def _row(label: str, g: pd.DataFrame) -> str:
    f = ss._fmt
    if g.empty:
        return f"| {label} | 0 | – | – | – | – | – | – | – |"
    return (f"| {label} | {len(g)} | {f(g['implied'].mean(), 1)} | {f(g['realised'].mean(), 1)} | "
            f"{f(g['gap'].mean(), 1)} | {f((g['gap'] > 0).mean() * 100, 0)} | "
            f"{f(g['gap'].quantile(0.05), 1)} | {f(g['seller_share'].mean())} | "
            f"{f(g['seller_share'].min())} |")


def to_markdown(df: pd.DataFrame, vix_rows: int, generated: str) -> str:
    f = ss._fmt
    lines = [f"# India VIX vs NIFTY realised volatility {VIX_STUDY_VERSION} (development period only)",
             "", f"Generated {generated}. Implied = India VIX close; realised = NIFTY's next "
             f"{WINDOW} trading days, annualised. Development: before {VALIDATION_FROM}; validation "
             "is held back. **A volatility study, not a trade: no option prices, margins, costs "
             "or slippage.**", ""]
    if df.empty:
        return "\n".join(lines + [f"No days with both India VIX and NIFTY ({vix_rows} VIX candles "
                                  "stored). If that is 0, Groww did not return VIX history for "
                                  "NSE-INDIAVIX."])
    lines += [f"Days: {len(df)}, {df.index[0]:%Y-%m-%d} to {df.index[-1]:%Y-%m-%d} "
              f"(about {len(df) // WINDOW} independent 21-day windows). VIX candles stored: "
              f"{vix_rows}.", "",
              f"First VIX close used: {f(df['implied'].iloc[0])} on {df.index[0]:%Y-%m-%d}; last: "
              f"{f(df['implied'].iloc[-1])} on {df.index[-1]:%Y-%m-%d}. Compare with NSE's own "
              "India VIX history to confirm the data is right.", "",
              *([] if 5 <= df["implied"].median() <= 100 else [
                  f"**WARNING: the median VIX close is {f(df['implied'].median(), 1)}; India VIX "
                  "normally sits between about 10 and 30. The stored series may not be India "
                  "VIX, so do not trust the results below.**", ""]),
              "Columns: average implied and realised volatility (%), average gap (points), % of "
              "days VIX was above realised, the worst 5% gap (points), average and worst seller "
              "share.", "",
              "| Group | Days | Implied | Realised | Gap | VIX above % | Worst 5% gap | "
              "Seller share | Worst seller share |", "|---|---|---|---|---|---|---|---|---|",
              _row("All days", df), "", "## 1. By year", "",
              "| Year | Days | Implied | Realised | Gap | VIX above % | Worst 5% gap | "
              "Seller share | Worst seller share |", "|---|---|---|---|---|---|---|---|---|"]
    for yr, g in df.groupby(df.index.year):
        lines.append(_row(str(yr), g))
    lines += ["", "## 2. By VIX level on the day", "",
              "| VIX level | Days | Implied | Realised | Gap | VIX above % | Worst 5% gap | "
              "Seller share | Worst seller share |", "|---|---|---|---|---|---|---|---|---|"]
    for lo, hi in LEVELS:
        g = df[(df["implied"] >= lo) & (df["implied"] < hi)]
        lines.append(_row(f"{lo}–{hi}" if hi < 1000 else f"{lo}+", g))
    worst = df.nsmallest(5, "gap")
    lines += ["", "## 3. The five worst days to have sold volatility", "",
              "| Day | VIX | Realised next 21 days | Gap |", "|---|---|---|---|"]
    lines += [f"| {d:%Y-%m-%d} | {f(r.implied, 1)} | {f(r.realised, 1)} | {f(r.gap, 1)} |"
              for d, r in worst.iterrows()]
    lines += ["", "## How to read this", "",
              "- A real premium shows a positive gap and seller share in **most years and most "
              "VIX levels**, not just on average.",
              "- The worst 5% and worst rows are the risk: a seller has to survive them, so any "
              "rule built on this needs a hard loss limit (defined-risk spreads, not naked selling).",
              "- Close-to-close realised volatility misses intraday swings, so it slightly "
              "understates what a seller lives through.", ""]
    return "\n".join(lines)
