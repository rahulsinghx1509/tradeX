"""AD-1.0 walk-forward: each month the agent scores every candidate on the months before,
picks a few, and trades them in the month it has not seen. Then the account rules decide which
of the picked trades really happen.

If you know SQL: `train_stats` is a GROUP BY candidate over the training months with SUM, COUNT,
STDDEV and "sum without the 2 largest"; `select` is ORDER BY score with a per-family DISTINCT;
`execute` walks the picked trades in time order like a cursor that keeps a running balance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app import agent as A
from app.agent.regime import Day


def add_regimes(trades: pd.DataFrame, info: dict[date, Day]) -> pd.DataFrame:
    """One boolean column per market condition: did the trade's entry day match it?"""
    t = trades.copy()
    for c in ("track", "base", "family", "structure", "reason", "live"):
        if c in t:
            t[c] = t[c].astype(str).astype("category")  # 1 GB server: small repeated strings
    for r in A.I_REGIMES:
        t[r] = [bool(info.get(d) and info[d].matches(r)) for d in t["entry_day"]]
    intraday = t["track"] == "I"
    t["LIVE_TREND"] &= intraday & (t["live"] == "T")
    t["LIVE_FLAT"] &= intraday & (t["live"] == "F")
    t["month"] = pd.to_datetime(t["entry_day"]).dt.to_period("M")
    return t


def candidates(trades: pd.DataFrame) -> list[tuple[str, str]]:
    bases = sorted(trades["base"].unique())
    return [(b, r) for b in bases for r in (A.I_REGIMES if b.startswith("I|") else A.REGIMES)]


def train_stats(train: pd.DataFrame, col: str) -> pd.DataFrame:
    """Per (base, regime): trades, net, PF, net without best TRAIN_DROP, score."""
    rows = []
    for r in A.I_REGIMES:
        sub = train.loc[train[r], ["base", "track", "family", col]]
        if sub.empty:
            continue
        v = sub[col]
        sub = sub.assign(pos=v.clip(lower=0), neg=-v.clip(upper=0))
        # rank inside each candidate, best trade = 0 (for "net without the best TRAIN_DROP")
        sub["rk"] = sub.groupby("base", observed=True)[col].rank(method="first", ascending=False) - 1
        sub["rest"] = np.where(sub["rk"] >= A.TRAIN_DROP, v, 0.0)
        g = sub.groupby("base", observed=True)
        agg = pd.DataFrame({
            "n": g.size(), "net": g[col].sum(), "mean": g[col].mean(), "std": g[col].std(ddof=1),
            "win": g["pos"].sum(), "loss": g["neg"].sum(), "net_drop": g["rest"].sum(),
            "track": g["track"].first(), "family": g["family"].first()})
        agg.loc[agg["n"] <= A.TRAIN_DROP, "net_drop"] = -np.inf
        agg["regime"] = r
        rows.append(agg.reset_index())
    if not rows:
        return pd.DataFrame(columns=["base", "regime", "n", "net", "pf", "score", "eligible"])
    s = pd.concat(rows, ignore_index=True)
    s["pf"] = np.where(s["loss"] > 0, s["win"] / s["loss"].where(s["loss"] > 0, 1),
                       np.where(s["win"] > 0, np.inf, 0.0))
    s["score"] = np.where(s["std"] > 0, s["mean"] / s["std"] * np.sqrt(s["n"]), -np.inf)
    s["eligible"] = ((s["n"] >= A.MIN_TRAIN_TRADES) & (s["pf"] >= A.TRAIN_PF) & (s["net"] > 0)
                     & (s["net_drop"] > 0) & np.isfinite(s["score"]))
    return s


def select(stats: pd.DataFrame, tracks=("I", "S")) -> list[dict]:
    """Best eligible candidates: distinct entry types (intraday), distinct holdings (swing)."""
    picks: list[dict] = []
    if stats.empty:
        return picks
    ok = stats[stats["eligible"]].sort_values(["score", "n"], ascending=False)
    for track, limit in (("I", A.PICK_INTRADAY), ("S", A.PICK_SWING)):
        if track not in tracks:
            continue
        seen: set[str] = set()
        for r in ok[ok["track"] == track].itertuples(index=False):
            if r.family in seen:
                continue
            seen.add(r.family)
            picks.append({"base": r.base, "regime": r.regime, "track": track, "score": float(r.score),
                          "train_n": int(r.n), "train_net": float(r.net), "train_pf": float(r.pf)})
            if len(seen) >= limit:
                break
    return picks


@dataclass
class Result:
    trades: pd.DataFrame
    months: list[dict] = field(default_factory=list)
    kills: int = 0
    blocked: dict[str, int] = field(default_factory=dict)


def _week(d: date) -> tuple[int, int]:
    y, w, _ = d.isocalendar()
    return y, w


def execute(month_trades: pd.DataFrame, picks: list[dict], col: str, state: dict,
            blocked: dict[str, int]) -> list[dict]:
    """Walk the picked candidates' trades of one month in time order under the account rules.
    `state` carries equity, peak and realised P&L by day/week across months."""
    if not picks or month_trades.empty:
        return []
    parts = []
    for rank, p in enumerate(picks):
        sub = month_trades[(month_trades["base"] == p["base"]) & month_trades[p["regime"]]]
        if not sub.empty:
            parts.append(sub.assign(rank=rank, regime=p["regime"]))
    if not parts:
        return []
    cand = pd.concat(parts).sort_values(["entry_time", "rank"])
    done: list[dict] = []
    open_until = {"I": None, "S": None}
    killed = False
    pending: list[dict] = []  # executed, not yet closed (realised at exit_time)

    def realise(now):
        nonlocal pending
        still = []
        for t in sorted(pending, key=lambda x: x["exit_time"]):
            if t["exit_time"] <= now:
                v = t["pnl"]
                state["equity"] += v
                state["peak"] = max(state["peak"], state["equity"])
                state["day"][t["exit_day"]] = state["day"].get(t["exit_day"], 0.0) + v
                wk = _week(t["exit_day"])
                state["week"][wk] = state["week"].get(wk, 0.0) + v
            else:
                still.append(t)
        pending = still

    for r in cand.to_dict("records"):
        now = r["entry_time"]
        realise(now)
        if killed:
            blocked["kill switch"] = blocked.get("kill switch", 0) + 1
            continue
        if state["peak"] - state["equity"] >= A.KILL_DD:
            killed = True
            state["kills"] += 1
            blocked["kill switch"] = blocked.get("kill switch", 0) + 1
            continue
        tr = r["track"]
        if open_until[tr] is not None and open_until[tr] > now:
            blocked["position already open"] = blocked.get("position already open", 0) + 1
            continue
        d = r["entry_day"]
        today = sum(1 for t in done if t["track"] == "I" and t["entry_day"] == d)
        if tr == "I" and today >= A.MAX_INTRADAY_DAY:
            blocked["2 intraday trades already today"] = blocked.get("2 intraday trades already today", 0) + 1
            continue
        if state["day"].get(d, 0.0) <= -A.DAY_LOSS:
            blocked["day loss limit"] = blocked.get("day loss limit", 0) + 1
            continue
        if state["week"].get(_week(d), 0.0) <= -A.WEEK_LOSS:
            blocked["week loss limit"] = blocked.get("week loss limit", 0) + 1
            continue
        r["pnl"] = r[col]
        done.append(r)
        pending.append(r)
        open_until[tr] = r["exit_time"]
    # trades still open at month end are realised before the next month starts
    realise(pd.Timestamp.max.tz_localize("UTC"))
    return done


def run(trades: pd.DataFrame, first_month: pd.Period, last_month: pd.Period, col: str = "net",
        tracks=("I", "S")) -> Result:
    """The agent from first_month (needs TRAIN_MONTHS before it) to last_month."""
    t = trades[trades["track"].isin(tracks)]
    state = {"equity": 0.0, "peak": 0.0, "day": {}, "week": {}, "kills": 0}
    out_trades: list[dict] = []
    months: list[dict] = []
    blocked: dict[str, int] = {}
    m = first_month
    while m <= last_month:
        train = t[(t["month"] >= m - A.TRAIN_MONTHS) & (t["month"] < m)]
        picks = select(train_stats(train, col), tracks)
        done = execute(t[t["month"] == m], picks, col, state, blocked)
        out_trades += done
        months.append({"month": str(m), "picks": picks, "trades": len(done),
                       "net": float(sum(x["pnl"] for x in done))})
        m += 1
    df = pd.DataFrame(out_trades)
    return Result(df, months, state["kills"], blocked)


def next_picks(trades: pd.DataFrame, month: pd.Period, col: str = "net", tracks=("I", "S")) -> list[dict]:
    t = trades[trades["track"].isin(tracks)]
    train = t[(t["month"] >= month - A.TRAIN_MONTHS) & (t["month"] < month)]
    return select(train_stats(train, col), tracks)


# ----------------------------------------------------------------------------- scoring
def _pf(x: pd.Series) -> float:
    loss = -x[x < 0].sum()
    return float(x[x > 0].sum() / loss) if loss > 0 else (math.inf if (x > 0).any() else 0.0)


def max_dd(x: pd.Series) -> float:
    eq = A.ACCOUNT + x.cumsum().to_numpy()
    peak = np.maximum.accumulate(np.concatenate([[A.ACCOUNT], eq]))[1:]
    return float((peak - eq).max()) if len(eq) else 0.0


def summarize(res: Result, res_2x: Result | None) -> dict:
    df = res.trades
    if df.empty:
        return {"trades": 0, "verdict": "FAIL", "checks": {"trades": False}, "months": res.months}
    df = df.sort_values("exit_time")
    net = df["pnl"].reset_index(drop=True)
    years = df.groupby(pd.to_datetime(df["exit_day"]).dt.year)["pnl"].agg(["sum", "count"])
    traded_months = [m for m in res.months if m["trades"]]
    pos_months = sum(1 for m in traded_months if m["net"] > 0)
    drop = net.sort_values(ascending=False).iloc[A.DROP_BEST:]
    net_2x = float(res_2x.trades["pnl"].sum()) if res_2x is not None and not res_2x.trades.empty else 0.0
    out = {
        "trades": len(df), "win": float((net > 0).mean()), "gross": float(df["gross"].sum()),
        "costs": float(df["costs"].sum()), "net": float(net.sum()), "avg": float(net.mean()),
        "pf": _pf(net), "max_dd": max_dd(net), "net_2x": net_2x, "net_drop": float(drop.sum()),
        "years": {int(y): (float(r["sum"]), int(r["count"])) for y, r in years.iterrows()},
        "months_traded": len(traded_months), "months_pos": pos_months,
        "months_none": sum(1 for m in res.months if not m["picks"]),
        "kills": res.kills, "blocked": res.blocked, "months": res.months,
        "by_track": df.groupby("track", observed=True)["pnl"].agg(["sum", "count"]).to_dict("index"),
    }
    out["checks"] = {
        "trades": out["trades"] >= A.MIN_TRADES,
        "pf": out["pf"] >= A.PF_MIN,
        "dd": out["max_dd"] < A.DD_MAX * A.ACCOUNT,
        "years": all(v > 0 for v, _ in out["years"].values()),
        "slip2x": net_2x > 0,
        "drop5": out["net_drop"] > 0,
        "months": pos_months * 2 > len(traded_months),
    }
    out["verdict"] = "PASS" if all(out["checks"].values()) else "FAIL"
    return out
