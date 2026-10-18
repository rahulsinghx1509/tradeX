"""Option metrics per underlying and day, rebuilt from NSE's daily F&O file (table fo_daily).

For each underlying and trading day we pick ONE expiry: the nearest monthly expiry (one that
also has a future) at least MIN_DTE calendar days away. Monthly options exist for every one of
our underlyings in every year, so the series stays comparable over time. On that expiry:

- forward       close of the future with the same expiry (its settle price if it did not trade)
- atm_iv        implied volatility (annualised %, Black-76) of the strike nearest the forward;
                average of the call and the put when both traded
- straddle_pct  ATM call + put price as % of the forward: the move the market prices to expiry
- iv_skew       IV of the put nearest 95% of the forward minus IV of the call nearest 105%
                (volatility points; positive = downside protection costs more)
- pcr_oi        put open interest / call open interest (strikes we keep, see nse_bhavcopy.py)
- pcr_vol       put volume / call volume
- max_pain_pct  strike where option buyers would lose most at expiry, as % away from forward
- call_wall_pct / put_wall_pct  strike with the most call / put OI, as % away from forward
- oi_top_share  share of all option OI sitting on the single biggest strike

Only prices of contracts that traded that day are used: a contract with no trades has a stale
close. Missing stays missing (NaN), never zero. Assumptions: interest rate 0 (discounting
ignored; for options of about a month this moves IV by well under 1%); IV is solved on the
day's close, which is not exactly simultaneous with the future's close. Strikes outside our
±15% storage band are not in fo_daily, so PCR and max pain cover that band only.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FoDaily

MIN_DTE = 7
METRICS = ["forward", "dte", "atm_iv", "straddle_pct", "iv_skew", "pcr_oi", "pcr_vol",
           "max_pain_pct", "call_wall_pct", "put_wall_pct", "oi_top_share", "strikes"]


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black76(forward: float, strike: float, t: float, vol: float, call: bool) -> float:
    """Undiscounted Black-76 option price."""
    if vol <= 0 or t <= 0:
        return max(0.0, (forward - strike) if call else (strike - forward))
    sd = vol * math.sqrt(t)
    d1 = (math.log(forward / strike) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    if call:
        return forward * _ncdf(d1) - strike * _ncdf(d2)
    return strike * _ncdf(-d2) - forward * _ncdf(-d1)


def implied_vol(price: float | None, forward: float, strike: float, t: float, call: bool) -> float:
    """Annualised IV in % by bisection, or NaN when the price is outside what any vol can give."""
    if price is None or not np.isfinite(price) or price <= 0 or forward <= 0 or strike <= 0 or t <= 0:
        return float("nan")
    intrinsic = max(0.0, (forward - strike) if call else (strike - forward))
    if price <= intrinsic or price >= (forward if call else strike):
        return float("nan")
    lo, hi = 1e-4, 5.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if black76(forward, strike, t, mid, call) > price:
            hi = mid
        else:
            lo = mid
    return round(100 * 0.5 * (lo + hi), 3)


def load_frame(session: Session, symbol: str, start: date | None = None,
               end: date | None = None) -> pd.DataFrame:
    """fo_daily rows of one underlying (only the columns the metrics need)."""
    q = select(FoDaily.trade_date, FoDaily.instrument, FoDaily.expiry_date, FoDaily.strike,
               FoDaily.close, FoDaily.settle, FoDaily.open_interest, FoDaily.volume,
               FoDaily.lot_size, FoDaily.file_format).where(FoDaily.symbol == symbol)
    if start:
        q = q.where(FoDaily.trade_date >= start)
    if end:
        q = q.where(FoDaily.trade_date <= end)
    cols = ["trade_date", "instrument", "expiry_date", "strike", "close", "settle",
            "open_interest", "volume", "lot_size", "file_format"]
    df = pd.DataFrame(session.execute(q).all(), columns=cols)
    for c in ("strike", "close", "settle", "open_interest", "volume", "lot_size"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def _nearest(strikes: pd.Series, target: float) -> float | None:
    s = strikes.dropna().unique()
    return None if len(s) == 0 else float(s[np.argmin(np.abs(s - target))])


def _traded_price(rows: pd.DataFrame) -> float | None:
    if rows.empty:
        return None
    r = rows.iloc[0]
    return float(r["close"]) if r["volume"] > 0 and np.isfinite(r["close"]) and r["close"] > 0 else None


def day_metrics(day: pd.DataFrame, trade_date: date) -> dict:
    out: dict = {m: float("nan") for m in METRICS}
    futs = day[day.instrument == "FUT"].sort_values("expiry_date")
    monthly = [e for e in futs.expiry_date if (e - trade_date).days >= MIN_DTE]
    if not monthly:
        return out
    expiry = monthly[0]
    fut = futs[futs.expiry_date == expiry].iloc[0]
    fwd = fut["close"] if fut["volume"] > 0 and np.isfinite(fut["close"]) else fut["settle"]
    if not np.isfinite(fwd) or fwd <= 0:
        return out
    t = (expiry - trade_date).days / 365.0
    out.update(forward=float(fwd), dte=float((expiry - trade_date).days))
    opts = day[(day.instrument != "FUT") & (day.expiry_date == expiry)]
    if opts.empty:
        return out
    ce, pe = opts[opts.instrument == "CE"], opts[opts.instrument == "PE"]
    out["strikes"] = float(opts.strike.nunique())

    atm = _nearest(opts.strike, fwd)
    c_px = _traded_price(ce[ce.strike == atm])
    p_px = _traded_price(pe[pe.strike == atm])
    ivs = [implied_vol(c_px, fwd, atm, t, True), implied_vol(p_px, fwd, atm, t, False)]
    ivs = [v for v in ivs if np.isfinite(v)]
    if ivs:
        out["atm_iv"] = float(np.mean(ivs))
    if c_px is not None and p_px is not None:
        out["straddle_pct"] = 100 * (c_px + p_px) / fwd

    k_put, k_call = _nearest(pe.strike, 0.95 * fwd), _nearest(ce.strike, 1.05 * fwd)
    if k_put is not None and k_call is not None:
        iv_p = implied_vol(_traded_price(pe[pe.strike == k_put]), fwd, k_put, t, False)
        iv_c = implied_vol(_traded_price(ce[ce.strike == k_call]), fwd, k_call, t, True)
        if np.isfinite(iv_p) and np.isfinite(iv_c):
            out["iv_skew"] = iv_p - iv_c

    c_oi, p_oi = ce.open_interest.sum(min_count=1), pe.open_interest.sum(min_count=1)
    if c_oi and p_oi and np.isfinite(c_oi) and np.isfinite(p_oi):
        out["pcr_oi"] = p_oi / c_oi
    c_v, p_v = ce.volume.sum(min_count=1), pe.volume.sum(min_count=1)
    if c_v and np.isfinite(c_v) and np.isfinite(p_v):
        out["pcr_vol"] = p_v / c_v

    strikes = np.sort(opts.strike.dropna().unique())
    ce_oi = ce.groupby("strike").open_interest.sum()
    pe_oi = pe.groupby("strike").open_interest.sum()
    if len(strikes) and (ce_oi.sum() + pe_oi.sum()) > 0:
        ck, cv = ce_oi.index.to_numpy(float), ce_oi.to_numpy(float)
        pk, pv = pe_oi.index.to_numpy(float), pe_oi.to_numpy(float)
        pain = [np.nansum(np.maximum(s - ck, 0) * cv) + np.nansum(np.maximum(pk - s, 0) * pv)
                for s in strikes]
        out["max_pain_pct"] = 100 * (strikes[int(np.argmin(pain))] / fwd - 1)
        total = ce_oi.add(pe_oi, fill_value=0)
        out["oi_top_share"] = float(total.max() / total.sum())
    if len(ce_oi) and ce_oi.max() > 0:
        out["call_wall_pct"] = 100 * (ce_oi.idxmax() / fwd - 1)
    if len(pe_oi) and pe_oi.max() > 0:
        out["put_wall_pct"] = 100 * (pe_oi.idxmax() / fwd - 1)
    return out


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """One row per trading day (index), METRICS as columns."""
    rows = {d: day_metrics(g, d) for d, g in df.groupby("trade_date")}
    out = pd.DataFrame.from_dict(rows, orient="index", columns=METRICS)
    out.index = pd.DatetimeIndex(out.index)
    return out.sort_index()


def units_check(df: pd.DataFrame) -> dict:
    """Did volume/OI units change between the old and new file? (NOT_VERIFIED until this runs.)

    Compares the nearest future's median OI in the last 20 old-format days with the first
    20 new-format days. A ratio near 1 means same units; near the lot size means one file
    counts shares and the other contracts.
    """
    futs = df[df.instrument == "FUT"].sort_values(["trade_date", "expiry_date"])
    near = futs.groupby("trade_date").head(1)
    old = near[near.file_format == "old"].tail(20)
    new = near[near.file_format == "udiff"].head(20)
    lot = new.lot_size.dropna()
    if old.empty or new.empty:
        return {"ratio": None, "lot": float(lot.median()) if len(lot) else None}
    ratio = new.open_interest.median() / old.open_interest.median()
    return {"ratio": float(ratio), "lot": float(lot.median()) if len(lot) else None,
            "old_oi": float(old.open_interest.median()), "new_oi": float(new.open_interest.median())}


def _fmt(x, nd=2) -> str:
    return "-" if x is None or not np.isfinite(x) else f"{x:.{nd}f}"


def to_markdown(per_symbol: dict[str, pd.DataFrame], units: dict[str, dict],
                vix: pd.Series | None, generated: str) -> str:
    lines = [
        "# Option metrics from NSE's daily F&O file",
        "",
        f"Generated {generated}. Nearest monthly expiry with at least {MIN_DTE} days left. "
        "Definitions in `backend/app/services/option_metrics.py`. A data check, not a study: "
        "no targets, no signals.",
        "",
        "## Coverage and typical values (medians)",
        "",
        "| Underlying | Days | First | Last | Days with ATM IV | ATM IV % | Straddle % | Skew "
        "| PCR (OI) | Max pain % |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for sym, m in per_symbol.items():
        if m.empty:
            lines.append(f"| {sym} | 0 | - | - | 0 | - | - | - | - | - |")
            continue
        lines.append(
            f"| {sym} | {len(m)} | {m.index[0]:%Y-%m-%d} | {m.index[-1]:%Y-%m-%d} | "
            f"{int(m.atm_iv.notna().sum())} | {_fmt(m.atm_iv.median(), 1)} | "
            f"{_fmt(m.straddle_pct.median())} | {_fmt(m.iv_skew.median(), 1)} | "
            f"{_fmt(m.pcr_oi.median())} | {_fmt(m.max_pain_pct.median())} |")

    lines += ["", "## Check 1: NIFTY ATM IV against India VIX", ""]
    nifty = per_symbol.get("NIFTY")
    if nifty is None or nifty.empty or vix is None or vix.empty:
        lines.append("Not possible: NIFTY option rows or India VIX candles missing.")
    else:
        j = pd.concat([nifty.atm_iv, vix.rename("vix")], axis=1, join="inner").dropna()
        if len(j) < 20:
            lines.append(f"Only {len(j)} common days; not enough.")
        else:
            corr = j.atm_iv.corr(j.vix)
            gap = (j.atm_iv - j.vix).median()
            ok = corr > 0.8 and abs(gap) < 3
            lines.append(f"{len(j)} common days: correlation {corr:.2f}, median ATM IV − VIX "
                         f"{gap:+.1f} points. {'PASS' if ok else 'CHECK'} "
                         "(expected: correlation above 0.8 and gap within ±3 points, since VIX is "
                         "built from near-month NIFTY option prices).")

    lines += ["", "## Check 2: did OI units change when NSE switched files (July 2024)?", "",
              "| Underlying | Old-file OI (median) | New-file OI (median) | Ratio | Lot size |",
              "|---|---|---|---|---|"]
    for sym, u in units.items():
        lines.append(f"| {sym} | {_fmt(u.get('old_oi'), 0)} | {_fmt(u.get('new_oi'), 0)} | "
                     f"{_fmt(u.get('ratio'))} | {_fmt(u.get('lot'), 0)} |")
    lines.append("")
    lines.append("Ratio near 1 = same units. Ratio near 1/lot or the lot size = one file counts "
                 "contracts, the other shares; PCR and OI shares are unaffected, absolute OI is.")
    return "\n".join(lines) + "\n"
