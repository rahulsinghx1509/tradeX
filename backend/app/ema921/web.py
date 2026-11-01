"""Flask trade viewer for the 9/21 EMA backtest: filters, KPIs, trade list, chart, cost card.

Read-only: every route is a GET, and nothing here talks to Groww. Results are computed from the
stored 1-minute data the first time a filter combination is asked for, then kept in memory
(the "cache trades" part of RK's brief), so moving through trades is instant.

Run it with `python scripts/ema921_viewer.py` (on the server it runs as the `ema` service, at
http://<server>/ema/).
"""

from __future__ import annotations

import threading
import time as _time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, time
from pathlib import Path

import pandas as pd
from flask import Flask, abort, jsonify, request, send_from_directory
from sqlalchemy.orm import Session

from app.ema921 import bars as B
from app.ema921 import engine as E
from app.ema921 import store as S
from app.ema921.metrics import summarize

STATIC = Path(__file__).with_name("static")
IST_OFFSET = 19800  # seconds; the chart library shows UTC, so times are shifted to read as IST
CROSS_CHOICES = {"all": None, "1": frozenset({1}), "2": frozenset({2}),
                 "3": frozenset(range(3, 100))}
SPOT_REFRESH_SECONDS = 600
RESULT_CACHE = 24
OPTION_CACHE = 1500


class _LruOptions:
    """DbOptionData with a bounded cache of option-day candles shared by every run."""

    def __init__(self, session_factory: Callable[[], Session]):
        self._factory = session_factory
        with session_factory() as s:
            base = S.DbOptionData(s)
        self._expiries, self._contracts = base._expiries, base._contracts
        self._cache: OrderedDict[tuple[str, date], pd.DataFrame | None] = OrderedDict()
        self._lock = threading.Lock()

    def expiries(self):
        return self._expiries

    def contract(self, expiry, strike, option_type):
        return self._contracts.get((expiry, float(strike), option_type))

    def minutes(self, symbol, day):
        key = (symbol, day)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        with self._factory() as s:
            df = S.load_minutes(s, symbol, day, day)
        df = None if df.empty else df
        with self._lock:
            self._cache[key] = df
            while len(self._cache) > OPTION_CACHE:
                self._cache.popitem(last=False)
        return df


class Backtests:
    """Runs and caches backtests for the viewer."""

    def __init__(self, session_factory: Callable[[], Session], start: date, end: date):
        self.factory, self.start, self.end = session_factory, start, end
        self._lock = threading.Lock()
        self._results: OrderedDict[tuple, E.Result] = OrderedDict()
        self._spot: pd.DataFrame | None = None
        self._spot_at = 0.0
        self._options: _LruOptions | None = None
        self._bars: dict[int, pd.DataFrame] = {}

    def _refresh(self) -> None:
        if self._spot is not None and _time.monotonic() - self._spot_at < SPOT_REFRESH_SECONDS:
            return
        with self.factory() as s:
            spot = S.load_spot(s, self.start, self.end)
        if self._spot is None or len(spot) != len(self._spot):  # new data loaded: start over
            self._results.clear()
            self._bars.clear()
            self._options = _LruOptions(self.factory)
        self._spot, self._spot_at = spot, _time.monotonic()

    def spot(self) -> pd.DataFrame:
        with self._lock:
            self._refresh()
            return self._spot

    def bars(self, tf: int) -> pd.DataFrame:
        with self._lock:
            self._refresh()
            if tf not in self._bars:
                self._bars[tf] = B.add_emas(B.resample(self._spot, tf))
            return self._bars[tf]

    def result(self, cfg: E.Config) -> E.Result:
        key = (cfg.timeframe, cfg.start, cfg.cutoff, cfg.cross_numbers)
        with self._lock:
            self._refresh()
            if key in self._results:
                self._results.move_to_end(key)
                return self._results[key]
            res = E.run(self._spot, self._options, self.start, self.end, cfg)
            self._results[key] = res
            while len(self._results) > RESULT_CACHE:
                self._results.popitem(last=False)
            return res


# ----------------------------------------------------------------------------- request parsing
def parse_config(args) -> E.Config:
    try:
        tf = int(args.get("tf", 5))
        start = time.fromisoformat(args.get("start", "09:30"))
        cutoff = time.fromisoformat(args.get("cutoff", "14:30"))
    except ValueError:
        abort(400, "bad filter value")
    cross = args.get("cross", "all")
    if tf not in B.TIMEFRAMES or start not in E.START_CHOICES or cutoff not in E.CUTOFF_CHOICES \
            or cross not in CROSS_CHOICES:
        abort(400, "unknown filter value")
    return E.Config(timeframe=tf, start=start, cutoff=cutoff, cross_numbers=CROSS_CHOICES[cross])


def _mode(args) -> str:
    mode = args.get("mode", "ATM_BUY")
    if mode not in E.MODES:
        abort(400, "unknown mode")
    return mode


def _outcome_filter(trades: list[E.Trade], outcome: str) -> list[tuple[int, E.Trade]]:
    pairs = list(enumerate(trades))
    if outcome == "win":
        return [(i, t) for i, t in pairs if t.net_pnl > 0]
    if outcome == "loss":
        return [(i, t) for i, t in pairs if t.net_pnl <= 0]
    if outcome == "whipsaw":
        return [(i, t) for i, t in pairs if t.whipsaw]
    return pairs


def _iso(t) -> str | None:
    return None if t is None else pd.Timestamp(t).tz_convert("Asia/Kolkata").strftime("%Y-%m-%d %H:%M")


def trade_json(i: int, t: E.Trade) -> dict:
    return {
        "id": i, "day": t.day.isoformat(), "direction": "LONG" if t.direction > 0 else "SHORT",
        "side": t.side, "option_type": t.option_type, "strike": t.strike, "symbol": t.symbol,
        "expiry": t.expiry.isoformat(), "dte": t.dte, "cross_number": t.cross_number,
        "signal_time": _iso(t.signal_time), "entry_time": _iso(t.entry_time),
        "exit_time": _iso(t.exit_time), "entry_price": t.entry_price, "entry_fill": t.entry_fill,
        "exit_price": t.exit_price, "exit_fill": t.exit_fill, "exit_reason": t.exit_reason,
        "nifty_entry": t.nifty_entry, "nifty_exit": t.nifty_exit, "bars_held": t.bars_held,
        "whipsaw": t.whipsaw, "stale_exit": t.stale_exit, "gross_pnl": t.gross_pnl,
        "slippage": t.slippage, "charges": t.charges, "net_pnl": t.net_pnl, "mae": t.mae,
    }


def _chart_time(ts) -> int:
    return int(pd.Timestamp(ts).timestamp()) + IST_OFFSET


def _candles(df: pd.DataFrame, time_col: str) -> list[dict]:
    out = []
    for r in df.itertuples(index=False):
        o, h, lo, c = (getattr(r, k) for k in ("open", "high", "low", "close"))
        if any(pd.isna(x) for x in (o, h, lo, c)):
            continue  # a candle with a missing price is not drawn (never drawn as zero)
        out.append({"time": _chart_time(getattr(r, time_col)), "open": float(o), "high": float(h),
                    "low": float(lo), "close": float(c)})
    return out


# ----------------------------------------------------------------------------- the app
def create_app(backtests: Backtests) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["JSON_SORT_KEYS"] = False

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    @app.get("/<any('lightweight-charts.js'):name>")
    def static_file(name: str):
        return send_from_directory(STATIC, name)

    @app.get("/api/meta")
    def meta():
        spot = backtests.spot()
        return jsonify({
            "range": [backtests.start.isoformat(), backtests.end.isoformat()],
            "has_data": not spot.empty,
            "spot_minutes": int(len(spot)),
            "timeframes": list(B.TIMEFRAMES),
            "starts": [t.strftime("%H:%M") for t in E.START_CHOICES],
            "cutoffs": [t.strftime("%H:%M") for t in E.CUTOFF_CHOICES],
            "modes": [{"id": m, "label": E.MODE_LABELS[m]} for m in E.MODES],
            "lot_size": E.Config().lot_size,
        })

    @app.get("/api/trades")
    def trades():
        cfg = parse_config(request.args)
        mode = _mode(request.args)
        outcome = request.args.get("outcome", "all")
        if outcome not in ("all", "win", "loss", "whipsaw"):
            abort(400, "unknown outcome")
        if backtests.spot().empty:
            return jsonify({"has_data": False, "trades": [], "summary": None, "modes": []})
        res = backtests.result(cfg)
        chosen = _outcome_filter(res.trades[mode], outcome)
        compare = [{"mode": m, "label": E.MODE_LABELS[m],
                    **summarize(res.trades[m], cfg.capital).as_dict()} for m in E.MODES]
        return jsonify({
            "has_data": True,
            "summary": summarize([t for _, t in chosen], cfg.capital).as_dict(),
            "modes": compare,
            "trades": [trade_json(i, t) for i, t in chosen],
            "no_trade_days": len(res.no_trade_days), "days_tested": len(res.days_tested),
            "skips": len([s for s in res.skips if s.mode == mode]),
        })

    @app.get("/api/trade/<int:trade_id>")
    def trade(trade_id: int):
        cfg = parse_config(request.args)
        mode = _mode(request.args)
        res = backtests.result(cfg)
        if not 0 <= trade_id < len(res.trades[mode]):
            abort(404, "no such trade")
        t = res.trades[mode][trade_id]
        bars = backtests.bars(cfg.timeframe)
        day = bars[bars["day"] == t.day]
        opt = backtests._options.minutes(t.symbol, t.day) if backtests._options else None
        opt = opt if opt is not None else pd.DataFrame(columns=["time", "open", "high", "low", "close"])
        signal_bar = day[day["bar_close"] == pd.Timestamp(t.signal_time)]
        exit_bar = day[day["bar_close"] == pd.Timestamp(t.exit_time)] if t.exit_reason != \
            "SQUARE_OFF" else day[day["bar_start"] <= pd.Timestamp(t.exit_time)].tail(1)
        markers = []
        if len(signal_bar):
            markers.append({"time": _chart_time(signal_bar["bar_start"].iloc[0]),
                            "kind": "entry", "direction": "LONG" if t.direction > 0 else "SHORT"})
        if len(exit_bar):
            markers.append({"time": _chart_time(exit_bar["bar_start"].iloc[0]), "kind": "exit",
                            "reason": t.exit_reason})
        return jsonify({
            "trade": trade_json(trade_id, t),
            "spot": _candles(day, "bar_start"),
            "ema9": [{"time": _chart_time(s), "value": float(v)} for s, v in zip(day["bar_start"],
                                                                                   day["ema9"],
                                                                                   strict=True)],
            "ema21": [{"time": _chart_time(s), "value": float(v)} for s, v in zip(day["bar_start"],
                                                                                    day["ema21"],
                                                                                    strict=True)],
            "markers": markers,
            "option": _candles(opt, "time"),
            "option_markers": [{"time": _chart_time(t.entry_time), "kind": "entry"},
                               {"time": _chart_time(t.exit_time), "kind": "exit"}],
        })

    @app.errorhandler(400)
    @app.errorhandler(404)
    def _err(e):
        return jsonify({"error": getattr(e, "description", str(e))}), e.code

    return app
