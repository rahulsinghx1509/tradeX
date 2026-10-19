"""One-shot screen of who-holds-what readings from NSE's participant OI file (study PS-1.0).

Question: does the way FIIs, retail clients or Pros are positioned in index futures and index
options say anything about NIFTY's next 5/10/20 days, or its volatility next month?

The readings are market-wide (one value per day for the whole market), so each is tested the
way FS-1.1 tests market readings: NIFTY, one day per month (the month's last day), rank
correlation with what came next, Benjamini–Hochberg q < 0.10 across all tests, the |t| bar
below, and the same sign in both halves. Development period only (before VALIDATION_FROM).
Nothing is re-tuned after seeing results; a survivor gets one final test on held-back data.
"""

from __future__ import annotations

import pandas as pd

from app.services import feature_screen as fs
from app.services import participant_oi as po
from app.services import signal_study as ss

SCREEN_VERSION = "PS-1.0"
# Set on 150 random worlds before any real run (2026-10-19): 6 readings × 4 targets, all
# market tests. Noise passed |t| 3.0 in 8% of worlds, 3.3 in 4%, so the bar is 3.3 (as OS-1.0).
MIN_ABS_T_MARKET = 3.3
FEATURES = dict(po.FEATURES)
TARGETS = list(fs.TARGETS)


def build_table(base: pd.DataFrame, readings: pd.DataFrame) -> pd.DataFrame:
    """NIFTY's FS-1.1 table (targets) joined with the day's readings (missing stays missing)."""
    t = pd.DataFrame(index=base.index)
    t["symbol"] = base["symbol"]
    r = readings.reindex(base.index)
    for f in FEATURES:
        t[f] = r[f]
    for tgt in TARGETS:
        t[tgt] = base[tgt]
    return t[["symbol", *FEATURES, *TARGETS]]


def screen(dev: pd.DataFrame) -> pd.DataFrame:
    return fs.screen(dev, FEATURES, TARGETS, tuple(FEATURES), set(), fs.MIN_ABS_T_CS,
                     MIN_ABS_T_MARKET)


def to_markdown(dev: pd.DataFrame, res: pd.DataFrame, generated: str, days_loaded: int) -> str:
    f = ss._fmt
    lines = [f"# Participant positioning screen {SCREEN_VERSION} (development period only)", "",
             f"Generated {generated}. {len(FEATURES)} readings × {len(TARGETS)} targets, all tested "
             f"on NIFTY one day per month (rank correlation, IC). Benjamini–Hochberg q < "
             f"{fs.Q_MAX:g}; |t| ≥ {MIN_ABS_T_MARKET:g} (set on random worlds); same sign in "
             f"both halves. Development: before {fs.VALIDATION_FROM}; validation is held back. "
             "No trades, costs or slippage.", "",
             f"Participant OI days loaded: {days_loaded}. Development days with a reading: "
             f"{int(dev['fii_fut_long_share'].notna().sum())}.", ""]
    if "ic" not in res or res["ic"].dropna().empty:
        return "\n".join(lines + ["Not enough development months to score any reading."]) + "\n"
    surv = res[res["survives"]].sort_values("q")
    lines += [f"## 1. Survivors ({len(surv)})", ""]
    if surv.empty:
        lines.append("**None.** No positioning reading's link to what came next is strong "
                     "enough to rule out luck once all tests are counted.")
    else:
        lines += ["| Reading | Target | Months | IC | t | q | 1st half | 2nd half |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in surv.itertuples():
            lines.append(f"| {r.feature} | {r.target} | {int(r.months)} | {f(r.ic, 3)} | "
                         f"{f(r.t, 1)} | {f(r.q, 3)} | {f(r.ic_1st_half, 3)} | "
                         f"{f(r.ic_2nd_half, 3)} |")
    lines += ["", "## 2. Every reading (IC, t; * = survivor)", "",
              "| Reading | Meaning | " + " | ".join(TARGETS) + " |",
              "|---|---|" + "---|" * len(TARGETS)]
    for feat, meaning in FEATURES.items():
        cells = []
        for tgt in TARGETS:
            r = res[(res["feature"] == feat) & (res["target"] == tgt)]
            if r.empty or pd.isna(r["ic"].iloc[0]):
                cells.append("–")
                continue
            r = r.iloc[0]
            cells.append(f"{f(r['ic'], 3)} (t {f(r['t'], 1)}){'*' if r['survives'] else ''}")
        lines.append(f"| {feat} | {meaning} | " + " | ".join(cells) + " |")
    lines += ["", "## Notes", "",
              "- IC > 0: a higher reading came before a higher target (a rise for returns, more "
              "movement than recent volatility for vol_surprise).",
              "- About 60 development months: only a strong, steady link can pass; a weak one "
              "would need more years than exist.",
              "- Readings use shares only, so NSE lot-size changes do not move them.",
              "- Before this ran on real data it was run on 150 random worlds: with this bar, "
              "noise produced a survivor in about 1 world in 25.", ""]
    return "\n".join(lines) + "\n"
