"""One-shot screen of the option metrics rebuilt from NSE's daily F&O file (study OS-1.0).

Same scoring rules as the price/OI screen FS-1.1 (services/feature_screen.py): development
period only (before VALIDATION_FROM), cross-sectional monthly IC with Newey–West t across the
12 underlyings, NIFTY-only monthly test for market-wide readings, Benjamini–Hochberg q < 0.10,
the |t| bars calibrated on random prices, and the same sign in both halves. Nothing here is
re-tuned after seeing results; at most MAX_FINALISTS survivors go to one final test.

Metrics come from services/option_metrics.py (nearest monthly expiry with 7+ days left), plus
a few built from them on each day using only data known at that day's close:
- iv_rank_1y   where today's ATM IV sits in its own past year (0 = lowest, 1 = highest)
- iv_minus_hv20  ATM IV minus the stock's 20-day realised volatility (points)
- iv_chg_5d    ATM IV change over 5 days (points)
- pcr_oi_chg_5d  PCR (OI) change over 5 days

Targets: the four from FS-1.1 (returns over 5/10/20 days, vol_surprise against HV20) plus
- rv_vs_iv: log(realised volatility over the next month / ATM IV today). Negative = the
  stock moved less than its options priced (an option seller kept money). This is the target
  that matters for option strategies.

"Mechanical" pairs (linked by construction, never survivors): every IV-based metric against
both volatility targets; see MECHANICAL for why (found on random worlds before any real run).
So this screen can test IV metrics only against returns, and skew, PCR, max pain and OI walls
against everything. Section 3 of the report still shows the average size of rv_vs_iv: whether
options were usually overpriced matters even if no metric times it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services import feature_screen as fs
from app.services import signal_study as ss

SCREEN_VERSION = "OS-1.0"
# |t| bars, fixed on random worlds (tests/null_world.py) before any real run, 2026-10-18.
# 60 worlds: the largest cross-section |t| never reached 4.0 (95th percentile 3.4), so FS-1.1's
# 4.0 stays. Market-wide tests are 25 here (FS-1.1: 16); with FS-1.1's 3.0, noise produced a
# survivor in about 1 world in 12, so the bar is raised to 3.3 (about 1 in 20).
MIN_ABS_T_CS = fs.MIN_ABS_T_CS
MIN_ABS_T_MARKET = 3.3
IV_RANK_DAYS, IV_RANK_MIN = 252, 120

FEATURES: dict[str, tuple[str, str]] = {
    "atm_iv": ("IV", "ATM implied volatility (annual %)"),
    "iv_rank_1y": ("IV Percentile/Rank", "ATM IV's rank within its past year (0–1)"),
    "iv_minus_hv20": ("IV/HV", "ATM IV minus 20-day realised volatility (points)"),
    "iv_chg_5d": ("IV History", "ATM IV change over 5 days (points)"),
    "straddle_pct": ("Expected Move (ATM straddle)", "ATM call + put as % of the forward"),
    "iv_skew": ("IV Skew", "95% put IV minus 105% call IV (points)"),
    "pcr_oi": ("PCR", "put OI / call OI"),
    "pcr_oi_chg_5d": ("PCR", "PCR (OI) change over 5 days"),
    "pcr_vol": ("PCR", "put volume / call volume"),
    "max_pain_pct": ("Max Pain", "max-pain strike vs forward (%)"),
    "call_wall_pct": ("Strike OI", "biggest call-OI strike vs forward (%)"),
    "put_wall_pct": ("Strike OI", "biggest put-OI strike vs forward (%)"),
    "oi_top_share": ("OI Concentration", "share of option OI on the biggest strike"),
    # NIFTY's own readings, tested over time as a market-wide signal (one day per month).
    "mkt_pcr_oi": ("PCR", "NIFTY PCR (OI), market-wide test"),
    "mkt_iv_skew": ("IV Skew", "NIFTY IV skew, market-wide test"),
    "mkt_iv_rank_1y": ("IV Percentile/Rank", "NIFTY IV rank, market-wide test"),
    "mkt_max_pain_pct": ("Max Pain", "NIFTY max pain vs forward, market-wide test"),
    "mkt_iv_minus_hv20": ("IV/HV", "NIFTY ATM IV minus HV20, market-wide test"),
}
MARKET_FEATURES = tuple(f for f in FEATURES if f.startswith("mkt_"))
TARGETS = [*fs.TARGETS, "rv_vs_iv"]
# Every IV-based metric against both volatility targets. Found by running this screen on random
# worlds first (tests/null_world.py), where IV = true volatility × small measurement noise:
# - against vol_surprise, IV "predicts" because IV knows today's volatility better than HV20
#   does (IV change t ≈ 30 on pure noise);
# - against rv_vs_iv, measurement noise in IV reverts (IV rank / IV change t ≈ −15).
# Both are true of real data too, and neither is an edge against option prices.
IV_FAMILY = ("atm_iv", "iv_rank_1y", "iv_minus_hv20", "iv_chg_5d", "straddle_pct",
             "mkt_iv_rank_1y", "mkt_iv_minus_hv20")
MECHANICAL = {(f, t) for f in IV_FAMILY for t in ("vol_surprise", "rv_vs_iv")}


def _rolling_rank(x: pd.Series, n: int, min_periods: int) -> pd.Series:
    """Where each value sits among the previous n values including itself (0–1)."""
    return x.rolling(n, min_periods=min_periods).apply(
        lambda w: (w[:-1] < w[-1]).mean() if np.isfinite(w[-1]) else np.nan, raw=True)


def build_table(base: pd.DataFrame, opt: pd.DataFrame) -> pd.DataFrame:
    """Option features and targets for one underlying.

    base: that underlying's FS-1.1 table (hv20 and the four targets), indexed by day.
    opt:  its option metrics from option_metrics.compute, indexed by day.
    """
    o = opt.reindex(base.index)  # a day without option data stays missing
    t = pd.DataFrame(index=base.index)
    t["symbol"] = base["symbol"]
    t["atm_iv"] = o["atm_iv"]
    t["iv_rank_1y"] = _rolling_rank(o["atm_iv"], IV_RANK_DAYS, IV_RANK_MIN)
    t["iv_minus_hv20"] = o["atm_iv"] - base["hv20"]
    t["iv_chg_5d"] = o["atm_iv"] - o["atm_iv"].shift(5)
    for c in ("straddle_pct", "iv_skew", "pcr_oi", "pcr_vol", "max_pain_pct", "call_wall_pct",
              "put_wall_pct", "oi_top_share"):
        t[c] = o[c]
    t["pcr_oi_chg_5d"] = o["pcr_oi"] - o["pcr_oi"].shift(5)
    for m in MARKET_FEATURES:
        t[m] = t[m.removeprefix("mkt_")]  # only NIFTY's rows are used for these
    for tgt in fs.TARGETS:
        t[tgt] = base[tgt]
    fwd_rv = np.exp(base["vol_surprise"]) * base["hv20"]  # realised vol over D+2 … D+22
    t["rv_vs_iv"] = np.log(fwd_rv / o["atm_iv"])
    return t[["symbol", *FEATURES, *TARGETS]]


def dev_only(table: pd.DataFrame) -> pd.DataFrame:
    """fs.dev_only, plus the same no-leak cut for rv_vs_iv (same forward window)."""
    dev = fs.dev_only(table)
    parts = []
    for _, g in dev.groupby("symbol"):
        g = g.copy()
        left = np.arange(len(g))[::-1]
        g.loc[left < fs.VOL_WINDOW + 1, "rv_vs_iv"] = np.nan
        parts.append(g)
    return pd.concat(parts) if parts else dev


def screen(dev: pd.DataFrame) -> pd.DataFrame:
    return fs.screen(dev, FEATURES, TARGETS, MARKET_FEATURES, MECHANICAL,
                     MIN_ABS_T_CS, MIN_ABS_T_MARKET)


def premium_summary(dev: pd.DataFrame) -> pd.DataFrame:
    """Per underlying: how often the next month moved less than ATM IV priced."""
    d = dev.dropna(subset=["rv_vs_iv"])
    g = d.groupby("symbol")["rv_vs_iv"]
    return pd.DataFrame({"days": g.size(), "median": g.median(),
                         "share_below": g.apply(lambda x: float((x < 0).mean())),
                         "worst_5pct": g.quantile(0.95)})


def to_markdown(dev: pd.DataFrame, res: pd.DataFrame, generated: str) -> str:
    f = ss._fmt
    lines = [f"# Option metric screen {SCREEN_VERSION} (development period only)", "",
             f"Generated {generated}. {len(FEATURES)} metrics × {len(TARGETS)} targets. Same "
             f"rules as {fs.SCREEN_VERSION}: rank correlation (IC) across the 12 underlyings "
             f"each day, averaged per month; market-wide (mkt_) on NIFTY, one day per month; "
             f"Benjamini–Hochberg q < {fs.Q_MAX:g}; |t| ≥ {MIN_ABS_T_CS:g} across "
             f"underlyings / {MIN_ABS_T_MARKET:g} market-wide (set on random worlds); same sign "
             "in both halves. "
             f"Development: before {fs.VALIDATION_FROM}; validation is held back. "
             "No trades, costs or slippage.", ""]
    if "ic" not in res or res["ic"].dropna().empty:
        return "\n".join(lines + ["Not enough development months to score any metric."]) + "\n"
    surv = res[res["survives"]].sort_values("q")
    lines += [f"## 1. Survivors ({len(surv)})", ""]
    if surv.empty:
        lines.append("**None.** No option metric's link to what came next is strong enough to "
                     "rule out luck once all tests are counted.")
    else:
        lines += ["| Metric | Target | Test | IC | t | q | 1st half | 2nd half | Years same sign |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in surv.itertuples():
            yrs = "–" if pd.isna(r.years_same_sign) else f"{r.years_same_sign * 100:.0f}%"
            lines.append(f"| {r.feature} | {r.target} | {r.test} | {f(r.ic, 3)} | {f(r.t, 1)} | "
                         f"{f(r.q, 3)} | {f(r.ic_1st_half, 3)} | {f(r.ic_2nd_half, 3)} | {yrs} |")
    lines += ["", "## 2. Every metric (IC, t; * = survivor, m = linked by construction)", "",
              "| Metric | Catalog item | Meaning | " + " | ".join(TARGETS) + " |",
              "|---|---|---|" + "---|" * len(TARGETS)]
    for feat, (cat, meaning) in FEATURES.items():
        cells = []
        for tgt in TARGETS:
            r = res[(res["feature"] == feat) & (res["target"] == tgt)]
            if r.empty or pd.isna(r["ic"].iloc[0]):
                cells.append("–")
                continue
            r = r.iloc[0]
            mark = "*" if r["survives"] else " m" if r["mechanical"] else ""
            cells.append(f"{f(r['ic'], 3)} (t {f(r['t'], 1)}){mark}")
        lines.append(f"| {feat} | {cat} | {meaning} | " + " | ".join(cells) + " |")
    prem = premium_summary(dev)
    lines += ["", "## 3. Were options usually overpriced? (rv_vs_iv, development days)", "",
              "rv_vs_iv = log(next month's realised volatility / ATM IV). Below 0 = the stock "
              "moved less than its options priced. Not a test of timing, just the average level; "
              "the worst 5% column is the tail an option seller must survive.", "",
              "| Underlying | Days | Median | Days below 0 | Worst 5% |", "|---|---|---|---|---|"]
    for sym, r in prem.iterrows():
        lines.append(f"| {sym} | {int(r['days'])} | {f(r['median'])} | "
                     f"{f(r['share_below'] * 100, 0)}% | {f(r['worst_5pct'])} |")
    lines += ["", "## Notes", "",
              f"- Rows used: {len(dev)} underlying-days; the real sample is the number of months.",
              "- ATM IV is solved on the day's option close (Black-76, rate 0); checked against "
              "India VIX (correlation 0.97, reports/option_metrics.md).",
              "- PCR and max pain cover strikes within ±15% of the price (what fo_daily keeps).",
              "- m: on random worlds where IV is just true volatility plus small measurement "
              "noise, IV-based metrics already 'predict' both volatility targets (|t| 15–30): IV "
              "knows today's volatility better than HV20, and its noise reverts. So IV metrics "
              "against volatility targets are never survivors here.",
              "- Before this ran on real data it was run on 60 random worlds "
              "(tests/null_world.py): with these bars, noise produced a survivor in about 1 "
              "world in 20.", ""]
    return "\n".join(lines) + "\n"
