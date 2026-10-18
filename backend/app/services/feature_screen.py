"""One-shot screen of every catalog metric that has history (study FS-1.1).

Instead of testing metrics one family at a time, this builds one table, with one row per
instrument per trading day and a column for every metric that can be computed from stored data
(price, trend, volatility, India VIX, futures basis, OI, volume and rollover). It then checks,
in a single report, how each metric relates to what happened next.

Every metric for day D uses only data known at D's close. Targets start from D+1's close, the
first price a decision made after D's close could get:
- ret_5d / ret_10d / ret_20d: underlying return (%) from D+1's close;
- vol_surprise: log(realised volatility over D+2 … D+22 / HV20 known on D). Positive = the
  market became more volatile than it had been (what an option buyer needs).

How a metric is scored, without being fooled by luck:
- Information coefficient (IC) = rank (Spearman) correlation between the metric and the target.
- Per-instrument metrics: on each day, the instruments are ranked against each other
  (cross-sectional IC), then averaged per calendar month. t = average monthly IC / its
  Newey–West standard error (neighbouring months share forward days); p from the normal curve.
  FS-1.0 correlated each month's days over time instead; on random prices that move together
  (as these 12 do) that shows strong false "reversal", so it was replaced before any use.
- Market-wide metrics (India VIX, days to expiry) are the same for every instrument, so they
  are tested over time on NIFTY with one observation per month (the month's last day), which
  keeps the forward windows from overlapping. t from the rank correlation across months.
- With about 100 metric × target pairs, roughly 5 would pass p < 0.05 by luck alone. So p-values
  are corrected with Benjamini–Hochberg: q is the share of "winners" at that level expected to be
  false. A metric survives only with q < Q_MAX, |t| at or above a bar calibrated on random
  prices (MIN_ABS_T_CS / MIN_ABS_T_MARKET) **and** the same sign in both halves of the
  development period. Each instrument's own average target is taken out before ranking, so a
  stock that simply drifted up all period cannot make momentum look predictive.
Only the development period (before VALIDATION_FROM) is used; validation stays held back for one
final test of at most MAX_FINALISTS survivors combined into one rule.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.market_time import from_db, to_ist
from app.db.models import Instrument, MarketCandle
from app.services import indicators as ind
from app.services import oi_study
from app.services import signal_study as ss

SCREEN_VERSION = "FS-1.1"
VALIDATION_FROM = date(2025, 4, 1)  # same held-back period as every earlier study
HORIZONS = (5, 10, 20)
VOL_WINDOW = 21
Q_MAX = 0.10
# Pass bar on |t|, on top of BH. Calibrated on 40 simulated worlds of random prices shaped like
# these 12 (market correlation ~0.7, VIX-like series moving against the market): the largest
# |t| among the ~80 cross-section tests passed 4.0 in about 1 world in 20, among the market
# tests 2.8. Many metrics move together (RSI, EMA distance, DI), so luck arrives in clusters
# that BH alone lets through.
MIN_ABS_T_CS = 4.0
MIN_ABS_T_MARKET = 3.0
MAX_FINALISTS = 3
MIN_MONTHS = 12
MIN_NAMES = 5  # instruments needed on a day to rank them against each other
MARKET_SYMBOL = "NIFTY"
# Same value for every instrument on a day, so they cannot be ranked across instruments; tested
# over time on NIFTY instead, one non-overlapping observation per month.
MARKET_FEATURES = ("vix", "vix_minus_hv20", "vix_chg_5d", "dte")

# name -> (catalog metric it stands for, short meaning). Order = report order.
FEATURES: dict[str, tuple[str, str]] = {
    "ret_5d": ("Spot Price", "return over the last 5 days (%)"),
    "ret_20d": ("Spot Price", "return over the last 20 days (%)"),
    "dist_ema20_atr": ("Moving Averages", "close minus EMA20, in ATRs"),
    "dist_ema50_atr": ("Moving Averages", "close minus EMA50, in ATRs"),
    "ema20_vs_ema50": ("Moving Averages", "EMA20 above EMA50 (%)"),
    "dist_ema200_pct": ("Moving Averages", "close above EMA200 (%)"),
    "rsi14": ("RSI", "RSI 14"),
    "macd_hist_atr": ("MACD", "MACD histogram (12/26/9), in ATRs"),
    "adx14": ("ADX", "trend strength"),
    "di_diff": ("ADX", "+DI minus −DI"),
    "weekly_trend_pct": ("Weekly / Multi-Timeframe Trend", "weekly close above weekly EMA20 (%)"),
    "rs20_vs_nifty": ("Relative Strength vs NIFTY", "20-day return minus NIFTY's (%)"),
    "corr60_nifty": ("Sector/Index Correlation", "60-day correlation with NIFTY"),
    "hv20": ("Historical Volatility", "20-day realised volatility (annual %)"),
    "atr_pct": ("ATR", "ATR as % of price"),
    "hv_ratio": ("Historical Volatility", "HV20 / HV100 (volatility rising > 1)"),
    "vix": ("India VIX", "India VIX close"),
    "vix_minus_hv20": ("IV vs HV", "India VIX minus NIFTY HV20 (points)"),
    "vix_chg_5d": ("India VIX", "India VIX change over 5 days (%)"),
    "basis_pct": ("Basis", "front future above spot (%)"),
    "carry_annual_pct": ("Cost of Carry / Mispricing", "basis annualised by days to expiry (%)"),
    "oi_chg_5d_pct": ("OI Change", "roll-safe futures OI change over 5 days (%)"),
    "buildup_score": ("Long/Short Buildup", "sign(5-day price move) × 5-day OI change (%)"),
    "fut_vol_ratio": ("Volume vs Average Volume", "futures volume / its 20-day average"),
    "next_month_oi_share": ("Rollover %", "share of futures OI outside the front month"),
    "dte": ("Days to Expiry", "days to the front future's expiry"),
}
TARGETS = [f"ret_{h}d_fwd" for h in HORIZONS] + ["vol_surprise"]
# vol_surprise divides by HV20, so metrics built from recent volatility relate to it by
# construction (volatility mean-reverts). Real, but not an edge without option prices: shown,
# never a survivor.
MECHANICAL = {(f, "vol_surprise") for f in ("hv20", "hv_ratio", "atr_pct", "vix_minus_hv20",
                                             "corr60_nifty")}
# corr60_nifty: on random prices it tracks the noise in HV20, so it "predicts" vol_surprise too.


# --------------------------------------------------------------------------- loading
def futures_long(session: Session, provider: str, symbol: str) -> pd.DataFrame:
    """Daily close, volume and OI of every futures contract of `symbol` (active and expired)."""
    rows = session.execute(
        select(MarketCandle.candle_time, Instrument.id, Instrument.expiry_date, MarketCandle.close,
               MarketCandle.volume, MarketCandle.open_interest)
        .join(Instrument, Instrument.id == MarketCandle.instrument_id)
        .where(Instrument.provider == provider, Instrument.symbol == symbol,
               Instrument.instrument_type == "FUTURE", MarketCandle.timeframe == "1d",
               MarketCandle.quality_status == "VALID", MarketCandle.close.is_not(None)))
    return pd.DataFrame(
        [(pd.Timestamp(to_ist(from_db(t)).date()), c, pd.Timestamp(e) if e else pd.NaT,
          float(cl), None if v is None else float(v), None if o is None else float(o))
         for t, c, e, cl, v, o in rows],
        columns=["day", "contract", "expiry", "close", "volume", "oi"])


# --------------------------------------------------------------------------- features
def _hv(close: pd.Series, n: int) -> pd.Series:
    return np.log(close / close.shift(1)).rolling(n, min_periods=n).std(ddof=1) * np.sqrt(252) * 100


def futures_features(spot_close: pd.Series, fut: pd.DataFrame) -> pd.DataFrame:
    """Basis, carry, OI, volume and rollover columns per day (NaN where no futures data)."""
    cols = ["basis_pct", "carry_annual_pct", "oi_chg_5d_pct", "fut_vol_ratio",
            "next_month_oi_share", "dte"]
    if fut.empty:
        return pd.DataFrame(index=spot_close.index, columns=cols, dtype=float)
    f = fut.dropna(subset=["expiry"])
    f = f[f["expiry"] >= f["day"]].sort_values(["day", "expiry"])
    front = f.groupby("day").head(1).set_index("day")  # nearest expiry that traded that day
    out = pd.DataFrame(index=front.index)
    spot = spot_close.reindex(front.index)
    out["basis_pct"] = (front["close"] / spot - 1) * 100
    out["dte"] = (front["expiry"] - front.index).dt.days.astype(float)
    out["carry_annual_pct"] = out["basis_pct"] * 365 / out["dte"].clip(lower=1)
    # OI: roll-safe change (contracts that traded on both days), from the OI study.
    oi = oi_study.daily_oi(f.dropna(subset=["oi"])[["day", "contract", "oi"]])
    oi5 = oi["oi_change"].rolling(5, min_periods=5).sum() / oi["total_oi"].shift(5) * 100
    out["oi_chg_5d_pct"] = oi5.reindex(out.index)
    out["next_month_oi_share"] = 1 - front["oi"] / oi["total_oi"].reindex(out.index)
    vol = f.groupby("day")["volume"].sum(min_count=1)
    out["fut_vol_ratio"] = (vol / vol.rolling(20, min_periods=20).mean().shift(1)).reindex(out.index)
    return out.reindex(spot_close.index)[cols]


def build_table(symbol: str, spot: pd.DataFrame, fut: pd.DataFrame, nifty: pd.DataFrame | None,
                vix: pd.DataFrame | None) -> pd.DataFrame:
    """Every feature and target for one instrument, indexed by trading day."""
    close, high, low = spot["close"], spot["high"], spot["low"]
    atr = ind.atr(high, low, close, 14)
    ema20, ema50, ema200 = (ind.ema(close, n) for n in (20, 50, 200))
    adx, pdi, mdi = ind.adx(high, low, close, 14)
    macd = ind.ema(close, 12) - ind.ema(close, 26)
    t = pd.DataFrame(index=spot.index)
    t["ret_5d"] = (close / close.shift(5) - 1) * 100
    t["ret_20d"] = (close / close.shift(20) - 1) * 100
    t["dist_ema20_atr"] = (close - ema20) / atr
    t["dist_ema50_atr"] = (close - ema50) / atr
    t["ema20_vs_ema50"] = (ema20 / ema50 - 1) * 100
    t["dist_ema200_pct"] = (close / ema200 - 1) * 100
    t["rsi14"] = ind.rsi(close, 14)
    t["macd_hist_atr"] = (macd - macd.ewm(span=9, adjust=False).mean()) / atr
    t["adx14"], t["di_diff"] = adx, pdi - mdi
    # Weekly: a week counts from its Friday (same rule as the signal study).
    weekly = close.groupby(close.index.to_period("W-FRI")).last()
    w = pd.DataFrame({"available_from": [p.end_time.normalize() for p in weekly.index],
                      "wk": ((weekly / ind.ema(weekly, 20) - 1) * 100).to_numpy()})
    t["weekly_trend_pct"] = pd.merge_asof(pd.DataFrame({"day": t.index}), w, left_on="day",
                                          right_on="available_from")["wk"].to_numpy()
    if nifty is not None and len(nifty) and symbol != "NIFTY":
        ref = nifty["close"].reindex(t.index)  # no fill: a missing NIFTY day stays missing
        t["rs20_vs_nifty"] = t["ret_20d"] - (ref / ref.shift(20) - 1) * 100
        r_mine, r_ref = np.log(close / close.shift(1)), np.log(ref / ref.shift(1))
        t["corr60_nifty"] = r_mine.rolling(60, min_periods=60).corr(r_ref)
    else:
        t["rs20_vs_nifty"] = t["corr60_nifty"] = np.nan
    t["hv20"] = _hv(close, 20)
    t["atr_pct"] = atr / close * 100
    t["hv_ratio"] = t["hv20"] / _hv(close, 100)
    if vix is not None and len(vix):
        v = vix["close"].reindex(t.index)
        nifty_hv = _hv(nifty["close"], 20).reindex(t.index) if nifty is not None else np.nan
        t["vix"], t["vix_minus_hv20"] = v, v - nifty_hv
        t["vix_chg_5d"] = (v / v.shift(5) - 1) * 100
    else:
        t["vix"] = t["vix_minus_hv20"] = t["vix_chg_5d"] = np.nan
    ff = futures_features(close, fut)
    t = t.join(ff)
    t["buildup_score"] = np.sign(t["ret_5d"]) * t["oi_chg_5d_pct"]
    # Targets, from D+1's close: never visible on day D.
    entry = close.shift(-1)
    for h in HORIZONS:
        t[f"ret_{h}d_fwd"] = (close.shift(-1 - h) / entry - 1) * 100
    fwd_rv = _hv(close, VOL_WINDOW).shift(-1 - VOL_WINDOW)  # returns D+2 … D+22
    t["vol_surprise"] = np.log(fwd_rv / t["hv20"])
    t.insert(0, "symbol", symbol)
    return t[["symbol", *FEATURES, *TARGETS]]


def dev_only(table: pd.DataFrame) -> pd.DataFrame:
    """Development rows whose forward windows end before validation (no leak)."""
    cut = pd.Timestamp(VALIDATION_FROM)
    parts = []
    for _, g in table.groupby("symbol"):
        g = g[g.index < cut].copy()
        # Trading days left before the cut; a target needing more days reaches into validation.
        left = np.arange(len(g))[::-1]
        for h in HORIZONS:
            g.loc[left < h + 1, f"ret_{h}d_fwd"] = np.nan
        g.loc[left < VOL_WINDOW + 1, "vol_surprise"] = np.nan
        parts.append(g)
    return pd.concat(parts) if parts else table.iloc[0:0]


# --------------------------------------------------------------------------- scoring
def _p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2))


def benjamini_hochberg(p: pd.Series) -> pd.Series:
    """q-values: the false-discovery rate at which each test would just pass."""
    p = p.dropna()
    if p.empty:
        return p
    order = p.sort_values()
    m = len(order)
    q = order * m / np.arange(1, m + 1)
    q = q[::-1].cummin()[::-1].clip(upper=1.0)
    return q.reindex(p.index)


def daily_cs_ic(df: pd.DataFrame, feature: str, target: str) -> pd.Series:
    """Cross-sectional IC per day: rank correlation across instruments on the same day.

    Comparing instruments with each other on one day removes the market's common move, which
    is what makes a within-month comparison over time show reversal on pure random prices.
    """
    d = df[["symbol", feature, target]].dropna()
    d = d[d.groupby(level=0)[feature].transform("size") >= MIN_NAMES]
    if d.empty:
        return pd.Series(dtype=float)
    g = d.groupby(level=0)
    rf, rt = g[feature].rank(), g[target].rank()
    rf = rf - rf.groupby(level=0).transform("mean")
    rt = rt - rt.groupby(level=0).transform("mean")
    num = (rf * rt).groupby(level=0).sum()
    den = np.sqrt((rf ** 2).groupby(level=0).sum() * (rt ** 2).groupby(level=0).sum())
    return (num / den.where(den > 0)).dropna()


def monthly_ic(df: pd.DataFrame, feature: str, target: str) -> pd.Series:
    """Average cross-sectional IC of each calendar month (months with at least 10 days)."""
    ic = daily_cs_ic(df, feature, target)
    if ic.empty:
        return ic
    g = ic.groupby(ic.index.to_period("M"))
    return g.mean()[g.size() >= 10]


def market_months(df: pd.DataFrame, feature: str, target: str) -> pd.DataFrame:
    """Market-wide metric (same value for every instrument): NIFTY on each month's last day.

    One observation per month, so a 5–20 day target never overlaps the next one.
    """
    d = df[df["symbol"] == MARKET_SYMBOL][[feature, target]].dropna()
    if d.empty:
        return d
    last = d.groupby(d.index.to_period("M")).tail(1)
    return last.set_index(last.index.to_period("M"))


def _spearman(a: pd.Series, b: pd.Series) -> float:
    return a.rank().corr(b.rank()) if len(a) > 2 else np.nan


def _horizon(target: str) -> int:
    return VOL_WINDOW + 1 if target == "vol_surprise" else int(target.split("_")[1][:-1])


def newey_west_t(x: pd.Series, lags: int) -> float:
    """t-statistic of the mean, allowing neighbouring months to be correlated (overlapping
    forward windows)."""
    x = x.to_numpy(dtype=float)
    n, e = len(x), x - x.mean()
    var = (e @ e) / n
    for k in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    return float(x.mean() / math.sqrt(var / n)) if var > 0 else 0.0


def screen(dev: pd.DataFrame) -> pd.DataFrame:
    rows = []
    # Cross-section: take out each instrument's own average target over the period. Otherwise a
    # stock that happened to drift up all along (random luck over a few years) makes momentum
    # look predictive in every month, though nobody could know the drift in advance.
    cs = dev.copy()
    for tgt in TARGETS:
        cs[tgt] = dev[tgt] - dev.groupby("symbol")[tgt].transform("mean")
    for feat in FEATURES:
        for tgt in TARGETS:
            if feat in MARKET_FEATURES:
                m = market_months(dev, feat, tgt)
                n = len(m)
                if n < MIN_MONTHS:
                    rows.append({"feature": feat, "target": tgt, "test": "market", "months": n})
                    continue
                r = _spearman(m[feat], m[tgt])
                tstat = r * math.sqrt((n - 2) / max(1 - r * r, 1e-12)) if pd.notna(r) else 0.0
                half = n // 2
                rows.append({"feature": feat, "target": tgt, "test": "market", "months": n,
                             "ic": r, "t": tstat, "p": _p_two_sided(tstat),
                             "ic_1st_half": _spearman(m[feat].iloc[:half], m[tgt].iloc[:half]),
                             "ic_2nd_half": _spearman(m[feat].iloc[half:], m[tgt].iloc[half:]),
                             "years_same_sign": np.nan})
                continue
            ic = monthly_ic(cs, feat, tgt)
            n = len(ic)
            if n < MIN_MONTHS:
                rows.append({"feature": feat, "target": tgt, "test": "cross-section", "months": n})
                continue
            mean = ic.mean()
            tstat = newey_west_t(ic, math.ceil(_horizon(tgt) / 21))
            half = n // 2
            yearly = ic.groupby(ic.index.year).mean()
            rows.append({"feature": feat, "target": tgt, "test": "cross-section", "months": n,
                         "ic": mean, "t": tstat, "p": _p_two_sided(tstat),
                         "ic_1st_half": ic.iloc[:half].mean(), "ic_2nd_half": ic.iloc[half:].mean(),
                         "years_same_sign": float((np.sign(yearly) == np.sign(mean)).mean())})
    res = pd.DataFrame(rows)
    if "p" in res:
        res["q"] = benjamini_hochberg(res["p"])
        same = np.sign(res["ic_1st_half"]) == np.sign(res["ic_2nd_half"])
        mech = [(f, t) in MECHANICAL for f, t in zip(res["feature"], res["target"], strict=True)]
        res["mechanical"] = mech
        bar = np.where(res["test"] == "market", MIN_ABS_T_MARKET, MIN_ABS_T_CS)
        res["survives"] = ((res["q"] < Q_MAX) & (res["t"].abs() >= bar) & same
                           & ~res["mechanical"])
    else:
        res["q"], res["mechanical"], res["survives"] = np.nan, False, False
    return res


# --------------------------------------------------------------------------- report
def to_markdown(dev: pd.DataFrame, res: pd.DataFrame, coverage: dict, generated: str) -> str:
    f = ss._fmt
    lines = [f"# One-shot metric screen {SCREEN_VERSION} (development period only)", "",
             f"Generated {generated}. {len(FEATURES)} metrics × {len(TARGETS)} targets = "
             f"{len(FEATURES) * len(TARGETS)} tests. Per-instrument metrics: the instruments are "
             "ranked against each other on each day (rank correlation, IC), averaged per month. "
             "Market-wide metrics (India VIX, days to expiry): NIFTY, one day per month. "
             f"Corrected for testing many at once (Benjamini–Hochberg, q < {Q_MAX:g}) and a |t| "
             f"bar set on random prices ({MIN_ABS_T_CS:g} across instruments, "
             f"{MIN_ABS_T_MARKET:g} for market-wide). "
             f"Development: before {VALIDATION_FROM}; validation is held back. "
             "**Underlying only: no options, costs or slippage.**", "",
             "## Data used", "", "| Instrument | Days | First | Last | With futures data | With VIX |",
             "|---|---|---|---|---|---|"]
    for sym, c in sorted(coverage.items()):
        lines.append(f"| {sym} | {c['days']} | {c['first']} | {c['last']} | {c['fut']} | {c['vix']} |")
    if "ic" not in res or res["ic"].dropna().empty:
        return "\n".join(lines + ["", "Not enough development months to score any metric "
                                  f"(at least {MIN_MONTHS} needed)."])
    surv = res[res["survives"]].sort_values("q")
    lines += ["", f"## 1. Survivors ({len(surv)})", "",
              "A survivor passed the luck correction and the |t| bar and had the same sign in "
              "both halves. "
              f"At most {MAX_FINALISTS} go forward, combined into one rule with its pass bar "
              "written down before the one final test.", ""]
    if surv.empty:
        lines.append("**None.** No metric's link to what came next is strong enough to rule out "
                     "luck once all tests are counted.")
    else:
        lines += ["| Metric | Target | Test | IC | t | q | 1st half | 2nd half | Years same sign |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in surv.itertuples():
            lines.append(f"| {r.feature} | {r.target} | {r.test} | {f(r.ic, 3)} | {f(r.t, 1)} | "
                         f"{f(r.q, 3)} | "
                         f"{f(r.ic_1st_half, 3)} | {f(r.ic_2nd_half, 3)} | "
                         f"{f(r.years_same_sign * 100, 0)}% |")
    lines += ["", "## 2. Every metric (IC by target; * = survivor, m = linked by construction)", "",
              "IC runs from −1 to +1. Positive = higher metric, higher target. For returns, |IC| "
              "of 0.03–0.05 that holds every year is already useful; for volatility surprise, "
              "larger values are common.", "",
              "| Metric | Catalog item | Meaning | " + " | ".join(TARGETS) + " |",
              "|---|---|---|" + "---|" * len(TARGETS)]
    for feat, (cat, meaning) in FEATURES.items():
        cells = []
        for tgt in TARGETS:
            r = res[(res["feature"] == feat) & (res["target"] == tgt)]
            if r.empty or "ic" not in r or pd.isna(r["ic"].iloc[0]):
                cells.append("–")
                continue
            r = r.iloc[0]
            mark = "*" if r["survives"] else " m" if r["mechanical"] else ""
            cells.append(f"{f(r['ic'], 3)} (t {f(r['t'], 1)}){mark}")
        lines.append(f"| {feat} | {cat} | {meaning} | " + " | ".join(cells) + " |")
    lines += ["", "## Notes", "",
              f"- Rows used: {len(dev)} instrument-days. Each month counts once per test, so the "
              "real sample is the number of months, not days.",
              "- Ranking instruments against each other asks \"which of the 12 to favour\", not "
              "\"is the market going up\". The market-wide test asks the second question with "
              "about 60 monthly points, so only a strong link can pass.",
              "- FS-1.0 compared each month's days over time instead. Random prices that move "
              "together, as these 12 do, then show strong false reversal (IC about −0.2, t about "
              "−9), which is what its 50 survivors were. FS-1.1 replaces it.",
              "- Not in this screen: the option-chain metrics (IV, IV rank, PCR, Greeks, skew, "
              "max pain, strike OI, bid/ask), which have no history yet; and metrics that need a "
              "data source outside Groww (FII/DII, GIFT Nifty, global markets, events, ban list).",
              "- m: volatility surprise is measured against HV20, so HV20, HV ratio, ATR %, "
              "VIX minus HV20 and correlation with NIFTY relate to it by construction "
              "(volatility mean-reverts). True, but only useful with option prices, so these "
              "are never survivors.",
              "- Mispricing uses carry (annualised basis) without a risk-free rate series; a "
              "near-constant rate shifts every day equally, so the ranking is unaffected.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- final test
# The one FS-1.1 survivor: India VIX rising over 5 days -> NIFTY's next month more volatile than
# its HV20. Intended use: a NO TRADE filter for option selling, never a buy/sell signal.
# Everything below was fixed on 2026-10-18 before any held-back day was looked at.
FINAL_FEATURE, FINAL_TARGET = "vix_chg_5d", "vol_surprise"
FINAL_JUMP_QUANTILE = 0.80  # "VIX jumped" = 5-day VIX change above its development 80th pct
FINAL_BAR = {"min_jump_days": 30, "min_episodes": 3, "min_gap": 0.10, "min_hit_rate": 0.55}
EPISODE_GAP_DAYS = 10  # jump days at least this many trading days apart start a new episode


def final_test(nifty_table: pd.DataFrame) -> dict:
    """Held-back days of NIFTY only, with the threshold taken from the development period."""
    t = nifty_table[[FINAL_FEATURE, FINAL_TARGET]].dropna()
    cut = pd.Timestamp(VALIDATION_FROM)
    dev, val = dev_only(nifty_table.assign(symbol="NIFTY")), t[t.index >= cut]
    dev = dev[[FINAL_FEATURE, FINAL_TARGET]].dropna()
    info: dict = {"val_days": len(val), "first": val.index.min(), "last": val.index.max()}
    if dev.empty or val.empty:
        info.update(passed=False, checks={"Held-back days with VIX and a full next month": False})
        return info
    threshold = float(dev[FINAL_FEATURE].quantile(FINAL_JUMP_QUANTILE))
    jump = val[FINAL_FEATURE] > threshold
    pos = np.flatnonzero(jump.to_numpy())
    episodes = int(len(pos) > 0) + int((np.diff(pos) >= EPISODE_GAP_DAYS).sum()) if len(pos) else 0
    j, o = val.loc[jump, FINAL_TARGET], val.loc[~jump, FINAL_TARGET]
    dj, do = dev.loc[dev[FINAL_FEATURE] > threshold, FINAL_TARGET], \
        dev.loc[dev[FINAL_FEATURE] <= threshold, FINAL_TARGET]
    month_end = val.groupby(val.index.to_period("M")).tail(1)
    info.update(threshold=threshold, jump_days=int(jump.sum()), episodes=episodes,
                jump_mean=j.mean(), other_mean=o.mean(), gap=j.mean() - o.mean(),
                hit_rate=float((j > 0).mean()) if len(j) else np.nan,
                dev_gap=dj.mean() - do.mean(), dev_hit_rate=float((dj > 0).mean()),
                months=len(month_end), monthly_ic=_spearman(month_end[FINAL_FEATURE],
                                                            month_end[FINAL_TARGET]))
    b = FINAL_BAR
    checks = {
        f"At least {b['min_jump_days']} VIX-jump days": info["jump_days"] >= b["min_jump_days"],
        f"At least {b['min_episodes']} separate jump episodes": episodes >= b["min_episodes"],
        f"Next month more volatile after jumps than other days by at least {b['min_gap']:g} "
        "(log)": bool(info["gap"] >= b["min_gap"]),
        f"Next month above HV20 on at least {b['min_hit_rate']:.0%} of jump days":
            bool(info["hit_rate"] >= b["min_hit_rate"]),
        "Month-end rank correlation positive, as in development": bool(info["monthly_ic"] > 0),
    }
    info.update(checks=checks, passed=all(checks.values()))
    return info


def final_markdown(info: dict, generated: str) -> str:
    f = ss._fmt
    verdict = "PASSED" if info["passed"] else "FAILED"
    lines = [f"# Final test of the VIX-jump volatility warning ({SCREEN_VERSION}): {verdict}", "",
             f"Generated {generated}. Held-back period only (from {VALIDATION_FROM}), NIFTY. Rule: "
             f"on a day when India VIX's 5-day change is above its development "
             f"{FINAL_JUMP_QUANTILE:.0%} level, expect NIFTY's next {VOL_WINDOW} trading days to be "
             "more volatile than its HV20. Intended use: **no option selling on those days**. "
             "Not a buy/sell signal. The bar below was written in code before this ran.", "",
             "| Check | Result |", "|---|---|"]
    lines += [f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in info["checks"].items()]
    if "threshold" in info:
        lines += ["", "| | Development | Held back |", "|---|---|---|",
                  f"| Days with VIX and a full next month | – | {info['val_days']} "
                  f"({info['first']:%Y-%m-%d} to {info['last']:%Y-%m-%d}) |",
                  f"| VIX-jump threshold (5-day change, %) | {f(info['threshold'], 1)} | same |",
                  f"| Jump days / episodes | – | {info['jump_days']} / {info['episodes']} |",
                  f"| Volatility surprise after jumps minus other days (log) | "
                  f"{f(info['dev_gap'])} | {f(info['gap'])} |",
                  f"| Jump days followed by a more volatile month | "
                  f"{f(info['dev_hit_rate'] * 100, 0)}% | {f(info['hit_rate'] * 100, 0)}% |",
                  f"| Month-end rank correlation ({info['months']} months) | {f(0.405, 3)} | "
                  f"{f(info['monthly_ic'], 3)} |"]
    lines += ["", "A log gap of 0.10 means about 10% more realised volatility. Neighbouring jump "
              "days share most of their next month, so episodes matter more than days.", "",
              "This test is run once. If it failed, the rule is not changed and re-tested on the "
              "same data; that would be fitting to luck.", ""]
    return "\n".join(lines)
