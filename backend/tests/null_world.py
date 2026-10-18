"""A random world shaped like our 12 underlyings, for checking a screen finds nothing in noise.

Prices: daily returns with a common market factor (correlation ~0.7) and slowly changing
volatility, so volatility targets behave like real ones. Option metrics: ATM IV = the true
current volatility times measurement noise; every other option reading is pure noise with
some day-to-day persistence. Nothing here predicts returns, and nothing but the true
volatility (inside IV) says anything about future volatility.
"""

import numpy as np
import pandas as pd

from app.services import feature_screen as fs
from app.services import option_metrics as om
from app.services import option_screen as osc

SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "INFY",
           "TCS", "RELIANCE", "BHARTIARTL", "LT"]


def _ar1(rng, n, phi=0.9, scale=1.0):
    x = np.zeros(n)
    e = rng.normal(0, scale * np.sqrt(1 - phi ** 2), n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    return x


def world(seed: int, days: int = 1300, iv_noise: float = 0.08) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=days)
    market = rng.normal(0, 1, days)
    tables = []
    for sym in SYMBOLS:
        vol = 0.20 * np.exp(_ar1(rng, days, 0.98, 0.35))  # true annual vol, persistent
        z = 0.84 * market + 0.54 * rng.normal(0, 1, days)
        r = z * vol / np.sqrt(252)
        close = 100 * np.exp(np.cumsum(r))
        spread = np.abs(rng.normal(0, 1, days)) * vol / np.sqrt(252) * close
        spot = pd.DataFrame({"high": close + spread, "low": close - spread, "close": close},
                            index=idx)
        base = fs.build_table(sym, spot, pd.DataFrame(), None, None)
        opt = pd.DataFrame(index=idx, columns=om.METRICS, dtype=float)
        opt["atm_iv"] = 100 * vol * np.exp(rng.normal(0, iv_noise, days))
        opt["straddle_pct"] = opt["atm_iv"] * 0.8 * np.sqrt(30 / 365)
        for c, lvl, sc in (("iv_skew", 2, 1.5), ("pcr_oi", 0.9, 0.2), ("pcr_vol", 0.9, 0.2),
                           ("max_pain_pct", 0, 1.5), ("call_wall_pct", 4, 2),
                           ("put_wall_pct", -4, 2), ("oi_top_share", 0.15, 0.04)):
            opt[c] = lvl + _ar1(rng, days, 0.8, sc)
        tables.append(osc.build_table(base, opt))
    return pd.concat(tables)
