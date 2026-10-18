"""Open-interest (OI) study: what did the underlying do after each kind of futures OI build-up?

Every day is labelled by two moves over the last WINDOW trading days:
- price: the underlying's close (index value or stock cash price), so contract rolls don't
  create fake jumps;
- futures OI: the change in total OI across all listed monthly contracts. Each daily change
  counts only the contracts that traded on both days, so a contract expiring or a new one being
  listed does not show up as a jump. OI moves within ±OI_FLAT_PCT count as flat.

| Price | OI   | Label            | Usual reading                        |
|-------|------|------------------|--------------------------------------|
| up    | up   | LONG_BUILDUP     | new buyers: bullish                  |
| down  | down | LONG_UNWINDING   | buyers leaving: bearish              |
| down  | up   | SHORT_BUILDUP    | new sellers: bearish                 |
| up    | down | SHORT_COVERING   | sellers leaving: bullish, often short-lived |

The study asks whether those readings hold: forward return after 5 / 10 / 20 trading days,
measured from the next day's close (no look-ahead), against the baseline of all days.
Only the development period (before VALIDATION_FROM) is reported; validation is held back.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import from_db, to_ist
from app.db.models import Instrument, MarketCandle
from app.services import signal_study as ss

OI_STUDY_VERSION = "OI-1.0"
WINDOW = 5
OI_FLAT_PCT = 1.0
HORIZONS = (5, 10, 20)
# Same split as the earlier studies, fixed so every study holds back the same period.
VALIDATION_FROM = date(2025, 4, 1)
LABELS = ("LONG_BUILDUP", "SHORT_COVERING", "SHORT_BUILDUP", "LONG_UNWINDING", "FLAT_OI")
BULLISH = {"LONG_BUILDUP": True, "SHORT_COVERING": True, "SHORT_BUILDUP": False,
           "LONG_UNWINDING": False}


def futures_oi(session: Session, provider: str, symbol: str) -> pd.DataFrame:
    """Daily OI of every futures contract of `symbol`: columns day, contract, oi (long format)."""
    rows = session.execute(
        select(MarketCandle.candle_time, MarketCandle.instrument_id, MarketCandle.open_interest)
        .join(Instrument, Instrument.id == MarketCandle.instrument_id)
        .where(Instrument.provider == provider, Instrument.symbol == symbol,
               Instrument.instrument_type == "FUTURE", MarketCandle.timeframe == "1d",
               MarketCandle.quality_status == "VALID", MarketCandle.open_interest.is_not(None)))
    df = pd.DataFrame([(pd.Timestamp(to_ist(from_db(t)).date()), c, float(o)) for t, c, o in rows],
                      columns=["day", "contract", "oi"])
    return df


def daily_oi(long: pd.DataFrame) -> pd.DataFrame:
    """Total OI per day and the day's change over contracts that traded on both days."""
    if long.empty:
        return pd.DataFrame(columns=["total_oi", "oi_change"], dtype=float)
    wide = long.pivot_table(index="day", columns="contract", values="oi", aggfunc="last").sort_index()
    prev = wide.shift(1)
    both = wide.notna() & prev.notna()
    change = (wide - prev).where(both).sum(axis=1, min_count=1)
    total = wide.sum(axis=1, min_count=1)
    out = pd.DataFrame({"total_oi": total, "oi_change": change})
    out.iloc[0, out.columns.get_loc("oi_change")] = np.nan  # no previous day
    return out


def label_days(price: pd.Series, oi: pd.DataFrame) -> pd.DataFrame:
    """Price and OI change over WINDOW days and the build-up label, for days with both."""
    df = pd.concat([price.rename("close"), oi], axis=1, join="inner").sort_index()
    df["price_pct"] = (df["close"] / df["close"].shift(WINDOW) - 1) * 100
    # OI change over the window = sum of daily changes / total OI at the window's start.
    oi_sum = df["oi_change"].rolling(WINDOW, min_periods=WINDOW).sum()
    df["oi_pct"] = oi_sum / df["total_oi"].shift(WINDOW) * 100
    up, down = df["price_pct"] > 0, df["price_pct"] < 0
    oi_up, oi_down = df["oi_pct"] > OI_FLAT_PCT, df["oi_pct"] < -OI_FLAT_PCT
    lab = np.select([up & oi_up, down & oi_down, down & oi_up, up & oi_down],
                    ["LONG_BUILDUP", "LONG_UNWINDING", "SHORT_BUILDUP", "SHORT_COVERING"],
                    default="FLAT_OI")
    df["label"] = pd.Series(lab, index=df.index).where(df["price_pct"].notna() & df["oi_pct"].notna())
    # Forward return from the next day's close, so the label day itself is never traded.
    entry = df["close"].shift(-1)
    for h in HORIZONS:
        df[f"ret_{h}d_pct"] = (df["close"].shift(-1 - h) / entry - 1) * 100
    return df


def run(prices: dict[str, pd.Series], oi: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Labelled days for every symbol that has both price and OI, development period only."""
    parts = []
    for sym, p in prices.items():
        if sym not in oi or oi[sym].empty:
            continue
        d = label_days(p, daily_oi(oi[sym])).dropna(subset=["label"])
        d.insert(0, "symbol", sym)
        parts.append(d.reset_index(names="day"))
    if not parts:
        return pd.DataFrame()
    df = pd.concat(parts, ignore_index=True)
    df = df[df["day"].dt.date < VALIDATION_FROM]
    # A forward window that reaches into validation would leak it: drop those returns.
    left = df.groupby("symbol").cumcount(ascending=False)  # trading days left before the cut
    for h in HORIZONS:
        df.loc[left < h + 1, f"ret_{h}d_pct"] = np.nan
    return df


def to_markdown(df: pd.DataFrame, generated: str) -> str:
    f = ss._fmt
    lines = [f"# Open-interest study {OI_STUDY_VERSION} (development period only)", "",
             f"Generated {generated}. Labels use the last {WINDOW} trading days; OI moves within "
             f"±{OI_FLAT_PCT:g}% count as flat. Returns are measured from the next day's close. "
             f"Development: before {VALIDATION_FROM}; validation is held back. "
             "**Underlying only: no options, costs or slippage.**", ""]
    if df.empty:
        return "\n".join(lines + ["No days with both price and futures OI. Run "
                                  "`deploy/tradex.sh futures-history` first."])
    lines += [f"Days: {len(df)} across {df['symbol'].nunique()} instruments, "
              f"{df['day'].min():%Y-%m-%d} to {df['day'].max():%Y-%m-%d}.", "",
              "## 1. Forward return by label (all instruments)", "",
              "Edge = label's average minus the baseline average (all days). For bearish labels a "
              "useful signal has a *negative* edge.", "",
              "| Label | Usual reading | Days | % of days | " + " | ".join(
                  f"{h}d avg % | {h}d edge | {h}d t" for h in HORIZONS) + " |",
              "|---|---|---|---|" + "---|---|---|" * len(HORIZONS)]
    base = {h: df[f"ret_{h}d_pct"].dropna() for h in HORIZONS}
    for lab in LABELS:
        sub = df[df["label"] == lab]
        reading = {True: "bullish", False: "bearish"}.get(BULLISH.get(lab), "–")
        cells = []
        for h in HORIZONS:
            s = ss.stats(sub[f"ret_{h}d_pct"])
            edge = s.mean - base[h].mean() if s.mean is not None else None
            # t of the difference: label days vs all other days (Welch).
            other = df.loc[df["label"] != lab, f"ret_{h}d_pct"].dropna()
            t = _welch_t(sub[f"ret_{h}d_pct"].dropna(), other)
            cells.append(f"{f(s.mean)} | {f(edge)} | {f(t, 1)}")
        lines.append(f"| {lab} | {reading} | {len(sub)} | {f(len(sub) / len(df) * 100, 0)} | "
                     + " | ".join(cells) + " |")
    lines += ["", "Days overlap (a 10-day return is shared by neighbouring days) and instruments "
              "move together, so the real evidence is weaker than *t* suggests: treat |t| below "
              "about 3 as noise here.", "",
              "## 2. 10-day edge by instrument", "",
              "| Instrument | Days | " + " | ".join(LABELS[:4]) + " |", "|---|---|" + "---|" * 4]
    for sym, g in df.groupby("symbol"):
        b = g["ret_10d_pct"].mean()
        cells = []
        for lab in LABELS[:4]:
            v = g.loc[g["label"] == lab, "ret_10d_pct"].dropna()
            cells.append(f"{f(v.mean() - b)} ({len(v)})" if len(v) else "–")
        lines.append(f"| {sym} | {len(g)} | " + " | ".join(cells) + " |")
    lines += ["", "Cells: 10-day edge in % and (days).", "",
              "## 3. By year (10-day edge, all instruments)", "",
              "| Year | " + " | ".join(LABELS[:4]) + " |", "|---|" + "---|" * 4]
    for yr, g in df.groupby(df["day"].dt.year):
        b = g["ret_10d_pct"].mean()
        cells = [f(g.loc[g["label"] == lab, "ret_10d_pct"].mean() - b) for lab in LABELS[:4]]
        lines.append(f"| {yr} | " + " | ".join(cells) + " |")
    lines += ["", "A reading worth testing further has the expected sign in most years and most "
              "instruments, not just a large average.", ""]
    return "\n".join(lines)


def _welch_t(a: pd.Series, b: pd.Series) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return None if se == 0 else float((a.mean() - b.mean()) / se)
