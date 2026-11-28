"""PR-1.0 engine: build iron-fly trades from fo_daily closes, apply filters, cost them, score them.

Pure functions on pandas frames (no database), so tests can feed small hand-made frames.

frame:  fo_daily rows of NIFTY (columns trade_date, instrument, expiry_date, strike, close,
        settle, volume), as option_metrics.load_frame returns them.
spot:   NIFTY daily close, indexed by trading date (Timestamp).
vix:    India VIX daily close, indexed by trading date (Timestamp), may be None.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from app import premium as P
from app.ema921 import costs as C


@dataclass
class Leg:
    kind: str     # CE / PE
    strike: float
    side: int     # -1 sold, +1 bought
    entry: float  # close at entry


@dataclass
class Trade:
    candidate: str
    entry_day: date
    exit_day: date
    expiry: date
    spot_entry: float
    atm: float
    wing: float
    credit_pts: float          # raw closes: shorts - wings
    exit_pts: float            # raw value paid back at exit (>= 0 means paid)
    gross: float               # ₹, closes only, no slippage, no charges
    net: float                 # ₹ at 1x slippage + charges
    net_2x: float              # ₹ at 2x slippage + charges
    costs: float               # ₹ at 1x
    max_loss: float            # ₹ worst case at entry (wing - credit) * lot, before costs
    settle_fallback: int = 0   # exit legs valued at NSE's settle price (no trade that day)
    vj_blocked: bool = False
    vrp_ok: bool | None = None
    legs: list[Leg] = field(default_factory=list, repr=False)


# ----------------------------------------------------------------------------- data helpers
def trim(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only option rows of the 2 nearest expiries each day, and only the needed columns.

    Both candidates use the nearest expiry, or the next one when the nearest ends during the hold,
    so this loses nothing and keeps memory small on the 1 GB server."""
    opt = frame.loc[frame.instrument.isin(["CE", "PE"]),
                    ["trade_date", "instrument", "expiry_date", "strike", "close", "settle", "volume"]].copy()
    opt["trade_date"] = pd.to_datetime(opt.trade_date).dt.date
    opt["expiry_date"] = pd.to_datetime(opt.expiry_date).dt.date
    opt = opt[opt.expiry_date >= opt.trade_date]
    rank = opt.groupby("trade_date")["expiry_date"].rank(method="dense")
    return opt[rank <= 2].reset_index(drop=True)


class Book:
    """fo_daily option rows grouped by (day, expiry), each side indexed by strike."""

    def __init__(self, frame: pd.DataFrame):
        opt = trim(frame)
        self.days: list[date] = sorted(opt.trade_date.unique())
        self.expiries_by_day: dict[date, list[date]] = (
            opt.groupby("trade_date")["expiry_date"].apply(lambda s: sorted(s.unique())).to_dict())
        self.groups: dict[tuple[date, date], dict[str, pd.DataFrame]] = {}
        self.strikes: dict[tuple[date, date], np.ndarray] = {}
        for key, g in opt.groupby(["trade_date", "expiry_date"]):
            self.groups[key] = {kind: gg.drop_duplicates("strike").set_index("strike")[
                ["close", "settle", "volume"]] for kind, gg in g.groupby("instrument")}
            self.strikes[key] = np.sort(g.strike.unique())

    def _row(self, day, expiry, kind, strike):
        side = self.groups.get((day, expiry), {}).get(kind)
        if side is None or float(strike) not in side.index:
            return None
        r = side.loc[float(strike)]
        return float(r["close"]), float(r["settle"]), float(r["volume"])

    def traded(self, day, expiry, kind, strike) -> float | None:
        r = self._row(day, expiry, kind, strike)
        if r is None:
            return None
        close, _, vol = r
        ok = vol is not None and np.isfinite(vol) and vol > 0 and np.isfinite(close) and close > 0
        return float(close) if ok else None

    def exit_price(self, day, expiry, kind, strike) -> tuple[float | None, bool]:
        """Traded close, else NSE's settle price (flag True), else None. Never zero-filled."""
        px = self.traded(day, expiry, kind, strike)
        if px is not None:
            return px, False
        r = self._row(day, expiry, kind, strike)
        if r is not None and np.isfinite(r[1]) and r[1] > 0:
            return float(r[1]), True
        return None, False

    def next_day(self, day: date) -> date | None:
        i = np.searchsorted(self.days, day, side="right")
        return self.days[i] if i < len(self.days) else None

    def nearest_expiry_after(self, day: date, after: date) -> date | None:
        """Nearest expiry stored on `day` that is strictly later than `after`."""
        later = [e for e in self.expiries_by_day.get(day, []) if e > after]
        return later[0] if later else None


def _round_step(x: float, step: float = P.STRIKE_STEP) -> float:
    return float(round(x / step) * step)


def vj_blocked(vix: pd.Series | None, entry: date) -> bool:
    """VJ-1.0 on the trading day before entry. Missing VIX = NO_DATA = blocked."""
    if vix is None or vix.empty:
        return True
    v = vix.sort_index()
    v = v[v.index < pd.Timestamp(entry)]
    if len(v) < 6:
        return True
    if (pd.Timestamp(entry) - v.index[-1]).days > 5:  # stale: more than a long weekend behind
        return True
    chg = (v.iloc[-1] / v.iloc[-6] - 1) * 100
    return bool(not np.isfinite(chg) or chg > P.VJ_JUMP_PCT)


def hv20(spot: pd.Series, before: date) -> float | None:
    """NIFTY's 20-day close-to-close realised volatility (annualised %, to the day before)."""
    s = spot.sort_index()
    s = s[s.index < pd.Timestamp(before)]
    if len(s) < P.HV_DAYS + 1:
        return None
    r = np.log(s.iloc[-(P.HV_DAYS + 1):]).diff().dropna()
    return float(r.std(ddof=1) * math.sqrt(252) * 100)


def vrp_ok(vix: pd.Series | None, spot: pd.Series, entry: date) -> bool:
    """India VIX (day before entry) above HV20 (to the day before). Missing = False."""
    if vix is None or vix.empty:
        return False
    v = vix.sort_index()
    v = v[v.index < pd.Timestamp(entry)]
    hv = hv20(spot, entry)
    if v.empty or hv is None:
        return False
    return bool(v.iloc[-1] > hv)


# ----------------------------------------------------------------------------- trade building
def open_fly(book: Book, day: date, expiry: date, spot_close: float) -> tuple[list[Leg], float] | None:
    """The four legs at the day's close, or None if a leg did not trade / strikes are missing."""
    strikes = book.strikes.get((day, expiry))
    if strikes is None or len(strikes) == 0:
        return None
    atm = float(strikes[np.argmin(np.abs(strikes - spot_close))])
    ce, pe = book.traded(day, expiry, "CE", atm), book.traded(day, expiry, "PE", atm)
    if ce is None or pe is None:
        return None
    wing = max(P.MIN_WING, _round_step(ce + pe))
    lc, lp = book.traded(day, expiry, "CE", atm + wing), book.traded(day, expiry, "PE", atm - wing)
    if lc is None or lp is None:
        return None
    return [Leg("CE", atm, -1, ce), Leg("PE", atm, -1, pe),
            Leg("CE", atm + wing, +1, lc), Leg("PE", atm - wing, +1, lp)], wing


def _fill(price: float, side_buy: bool, mult: float) -> float:
    f = C.buy_fill if side_buy else C.sell_fill
    return f(price, P.SLIP_PCT * mult, P.SLIP_PTS * mult)


def _entry_cash(legs: list[Leg], day: date, mult: float) -> tuple[float, float]:
    """(cash in per unit after slippage, entry charges ₹)."""
    sell = sum(_fill(lg.entry, False, mult) for lg in legs if lg.side < 0)
    buy = sum(_fill(lg.entry, True, mult) for lg in legs if lg.side > 0)
    ch = C.charges(buy * P.LOT, sell * P.LOT, day, orders=4)["total"]
    return sell - buy, ch


def _close_cash(legs: list[Leg], exits: list[float], day: date, mult: float) -> tuple[float, float]:
    """Closing all legs at market: (cash per unit, ₹ charges). Shorts bought back, wings sold."""
    buy = sum(_fill(px, True, mult) for lg, px in zip(legs, exits, strict=True) if lg.side < 0)
    sell = sum(_fill(px, False, mult) for lg, px in zip(legs, exits, strict=True) if lg.side > 0)
    ch = C.charges(buy * P.LOT, sell * P.LOT, day, orders=4)["total"]
    return sell - buy, ch


def _settle_cash(legs: list[Leg], s: float) -> tuple[float, float]:
    """Cash settlement at expiry on NIFTY's close: (cash per unit, ₹ charges)."""
    cash, ch = 0.0, 0.0
    for lg in legs:
        iv = max(s - lg.strike, 0.0) if lg.kind == "CE" else max(lg.strike - s, 0.0)
        if iv <= 0:
            continue
        cash += lg.side * iv
        ch += P.SETTLE_BROKERAGE
        if lg.side > 0:
            ch += P.EXERCISE_STT * iv * P.LOT
    return cash, ch


def _spot_on(spot: pd.Series, day: date) -> float | None:
    v = spot.get(pd.Timestamp(day))
    return float(v) if v is not None and np.isfinite(v) else None


def build_a(book: Book, spot: pd.Series, vix: pd.Series | None,
            skipped: dict[str, int] | None = None) -> list[Trade]:
    """Candidate A (all weekend entries; vj_blocked flags them, variant() filters)."""
    sk = skipped if skipped is not None else {}
    out = []
    for d in book.days:
        if d < P.FIRST_DAY:
            continue
        nd = book.next_day(d)
        if nd is None or (nd - d).days < P.WEEKEND_GAP_DAYS:
            continue
        expiry = book.nearest_expiry_after(d, nd)
        s0 = _spot_on(spot, d)
        if expiry is None or s0 is None:
            sk["A: no expiry or NIFTY close"] = sk.get("A: no expiry or NIFTY close", 0) + 1
            continue
        got = open_fly(book, d, expiry, s0)
        if got is None:
            sk["A: a leg did not trade at entry"] = sk.get("A: a leg did not trade at entry", 0) + 1
            continue
        legs, wing = got
        exits, fallback = [], 0
        for lg in legs:
            px, fb = book.exit_price(nd, expiry, lg.kind, lg.strike)
            exits.append(px)
            fallback += int(fb)
        if any(px is None for px in exits):
            sk["A: no exit price"] = sk.get("A: no exit price", 0) + 1
            continue
        credit = -sum(lg.side * lg.entry for lg in legs)
        back = -sum(lg.side * px for lg, px in zip(legs, exits, strict=True))
        nets = {}
        cost1 = 0.0
        for mult in (1.0, 2.0):
            cin, c_in = _entry_cash(legs, d, mult)
            cout, c_out = _close_cash(legs, exits, nd, mult)
            nets[mult] = (cin + cout) * P.LOT - c_in - c_out
            if mult == 1.0:
                cost1 = (credit - back) * P.LOT - nets[mult]
        out.append(Trade("A", d, nd, expiry, s0, legs[0].strike, wing, credit, back,
                         gross=(credit - back) * P.LOT, net=nets[1.0], net_2x=nets[2.0],
                         costs=cost1, max_loss=(wing - credit) * P.LOT,
                         settle_fallback=fallback, vj_blocked=vj_blocked(vix, d), legs=legs))
    return out


def weekly_expiries(book: Book) -> list[date]:
    """Every expiry that was the nearest one on some trading day (weeklies + monthlies)."""
    return sorted({ex[0] for d, ex in book.expiries_by_day.items() if ex and ex[0] >= d})


def build_b(book: Book, spot: pd.Series, vix: pd.Series | None,
            skipped: dict[str, int] | None = None) -> list[Trade]:
    """Candidate B (all weekly entries; vj_blocked / vrp_ok flag them, variant() filters)."""
    sk = skipped if skipped is not None else {}
    out = []
    for prev in weekly_expiries(book):
        d = book.next_day(prev)
        if d is None or d < P.FIRST_DAY:
            continue
        expiry = book.nearest_expiry_after(d, d)
        if expiry is None or (expiry - d).days < P.MIN_DTE_B:
            sk["B: expiry too close"] = sk.get("B: expiry too close", 0) + 1
            continue
        s0, s_exp = _spot_on(spot, d), _spot_on(spot, expiry)
        if s0 is None:
            sk["B: no NIFTY close at entry"] = sk.get("B: no NIFTY close at entry", 0) + 1
            continue
        if s_exp is None:
            if expiry <= book.days[-1]:
                sk["B: no NIFTY close at expiry"] = sk.get("B: no NIFTY close at expiry", 0) + 1
            continue  # still open, or no settlement price: never guessed
        got = open_fly(book, d, expiry, s0)
        if got is None:
            sk["B: a leg did not trade at entry"] = sk.get("B: a leg did not trade at entry", 0) + 1
            continue
        legs, wing = got
        credit = -sum(lg.side * lg.entry for lg in legs)
        settle, c_set = _settle_cash(legs, s_exp)
        nets = {}
        for mult in (1.0, 2.0):
            cin, c_in = _entry_cash(legs, d, mult)
            nets[mult] = (cin + settle) * P.LOT - c_in - c_set
        gross = (credit + settle) * P.LOT
        out.append(Trade("B", d, expiry, expiry, s0, legs[0].strike, wing, credit, -settle,
                         gross=gross, net=nets[1.0], net_2x=nets[2.0], costs=gross - nets[1.0],
                         max_loss=(wing - credit) * P.LOT, vj_blocked=vj_blocked(vix, d),
                         vrp_ok=vrp_ok(vix, spot, d), legs=legs))
    return out


def variant(trades: list[Trade], name: str) -> list[Trade]:
    if name == "A":
        return [t for t in trades if t.candidate == "A" and not t.vj_blocked]
    if name == "A0":
        return [t for t in trades if t.candidate == "A"]
    if name == "B":
        return [t for t in trades if t.candidate == "B" and not t.vj_blocked and t.vrp_ok]
    if name == "B0":
        return [t for t in trades if t.candidate == "B"]
    raise ValueError(name)


def to_frame(trades: list[Trade]) -> pd.DataFrame:
    rows = []
    for t in trades:
        d = asdict(t)
        legs = d.pop("legs")
        d["legs"] = " ".join(f"{'S' if lg['side'] < 0 else 'B'} {lg['strike']:.0f}{lg['kind']}@"
                             f"{lg['entry']:.2f}" for lg in legs)
        rows.append(d)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- scoring
def _pf(x: pd.Series) -> float:
    loss = -x[x < 0].sum()
    return float(x[x > 0].sum() / loss) if loss > 0 else (math.inf if (x > 0).any() else 0.0)


def max_dd(x: pd.Series) -> float:
    """Largest fall of closed-trade equity from its high (₹, positive number)."""
    eq = P.ACCOUNT + x.cumsum()
    peak = np.maximum.accumulate(np.concatenate([[P.ACCOUNT], eq.to_numpy()]))[1:]
    return float((peak - eq.to_numpy()).max()) if len(eq) else 0.0


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0, "verdict": "FAIL", "checks": {"trades": False}}
    df = to_frame(trades).sort_values("exit_day")
    net = df["net"].reset_index(drop=True)
    years = df.groupby(pd.to_datetime(df["exit_day"]).dt.year)["net"].agg(["sum", "count"])
    drop = net.sort_values(ascending=False).iloc[P.DROP_BEST:]
    out = {
        "trades": len(df), "win": float((net > 0).mean()), "gross": float(df["gross"].sum()),
        "costs": float(df["costs"].sum()), "net": float(net.sum()), "avg": float(net.mean()),
        "pf": _pf(net), "max_dd": max_dd(net), "worst": float(net.min()), "best": float(net.max()),
        "net_2x": float(df["net_2x"].sum()), "pf_2x": _pf(df["net_2x"]),
        "net_drop": float(drop.sum()), "pf_drop": _pf(drop),
        "years": {int(y): (float(r["sum"]), int(r["count"])) for y, r in years.iterrows()},
        "fallback": int(df["settle_fallback"].sum()),
    }
    checks = {
        "trades": out["trades"] >= P.MIN_TRADES,
        "pf": out["pf"] >= P.PF_MIN,
        "dd": out["max_dd"] < P.DD_MAX * P.ACCOUNT,
        "years": all(v > 0 for v, _ in out["years"].values()),
        "slip2x": out["net_2x"] > 0,
        "drop5": out["net_drop"] > 0,
    }
    out["checks"] = checks
    out["verdict"] = "PASS" if all(checks.values()) else "FAIL"
    return out
