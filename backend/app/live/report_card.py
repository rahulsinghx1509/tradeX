"""ONE end-of-day PAPER report picture (PNG) for every trade of the day, whatever the strategy.

- Numbered trades from the signals service (signal_trades): "Trade #20261105-01 · ORB-1.0", one row each.
- The 9/21 EMA paper engine's trades (paper_trades, the same 5-minute cross in 3 modes) in their
  own card. The headline total adds up EVERY alerted trade, these legs included (RK 2026-11-03).

Numbers come straight from the database rows; nothing is recomputed. Missing shows as "–".
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date

import pandas as pd

from app.ema921 import engine as E
from app.ema921.report_card import (
    BLUE,
    BLUE_LIGHT,
    CARD,
    GREEN,
    GREEN_LIGHT,
    INK,
    LINE,
    MUTED,
    PAD,
    RED,
    RED_LIGHT,
    W,
    _Canvas,
    _font,
    _hm,
    _px,
    _rs,
    _short_rs,
)
from app.live import engine as LE

MODE_ICON = {"ITM_BUY": "ITM", "ATM_BUY": "ATM", "ATM_SELL": "SELL"}


def _net(r) -> float | None:
    return None if r.net_pnl is None else float(r.net_pnl)


def _why(r) -> str:
    if r.exit_time is None:
        return "still open"
    return E.EXIT_LABELS.get(r.exit_reason, r.exit_reason or "–")


def _cash(r, qty: int, net: float | None) -> str:
    money = None if r.entry_fill is None else float(r.entry_fill) * qty
    if r.side == "BUY":
        pct = f" · {net / money * 100:+.1f}%" if money and net is not None else ""
        return f"Invested {_rs(money)} · P&L {_rs(net, sign=True)}{pct}"
    return f"Premium got {_rs(money)} · P&L {_rs(net, sign=True)} · margin n/a"


def _layout(c: _Canvas, day: date, signals: list, ema: list) -> int:
    f_title, f_sub = _font(50, True), _font(30)
    f_h, f_b, f_bb, f_s = _font(40, True), _font(32), _font(32, True), _font(27)
    f_badge, f_badge_lbl = _font(34, True), _font(28)

    c.text((PAD + 8, c.y), "TradeX · Paper day report", f_title)
    c.y += 66
    c.text((PAD + 8, c.y), f"All strategies · {day:%a %d %b %Y}", f_sub, MUTED)
    c.y += 70

    signals = sorted(signals, key=LE._order)
    every = list(signals) + list(ema)  # every alerted trade counts in the headline
    nets = [n for n in (_net(r) for r in every) if n is not None]
    costs = sum(float(r.slippage or 0) + float(r.charges or 0) for r in every if r.status == "CLOSED")
    gross = sum(float(r.gross_pnl) for r in every if r.gross_pnl is not None)
    by_strat: dict = defaultdict(float)
    for r in signals:
        if _net(r) is not None:
            by_strat[r.strategy] += _net(r)
    for r in ema:
        if _net(r) is not None:
            by_strat["9/21 EMA x3"] += _net(r)

    # Total card: the numbered trades
    top = c.y
    total = sum(nets) if nets else None
    c.rrect((PAD, top, W - PAD, top + 230), 44, CARD)
    c.text((PAD + 40, top + 40), "Total P&L today · every alerted trade", f_b, MUTED)
    col = INK if total is None else GREEN if total > 0 else RED if total < 0 else INK
    c.text((PAD + 40, top + 90), _rs(total, sign=True), _font(76, True), col)
    c.text((PAD + 40, top + 182), f"gross {_rs(gross) if every else '–'} · slippage + charges "
           f"{_rs(costs) if every else '–'} · {len(nets)} closed of {len(every)}", f_s, MUTED)
    if total is not None:
        c.pill(W - PAD - 40, top + 135, "PROFIT" if total > 0 else "LOSS" if total < 0 else "FLAT",
               f_bb, col, GREEN_LIGHT if total > 0 else RED_LIGHT if total < 0 else LINE)
    c.y = top + 230 + 44

    best = max(by_strat.items(), key=lambda kv: kv[1]) if by_strat else None
    badges = [(str(len(every)), "Trades"),
              (f"{sum(1 for x in nets if x > 0)}/{len(every)}", "Winners"),
              (best[0] if best else "–", "Best strategy"),
              (_short_rs(-costs) if costs else "–", "Costs")]
    r_b, step = 66, (W - 2 * PAD) / 4
    for i, (val, lbl) in enumerate(badges):
        cx = PAD + step * i + step / 2
        c.circle(cx, c.y + r_b, r_b, BLUE)
        fb = f_badge
        while c.width(val, fb) > 2 * r_b - 16 and fb.size > 18:
            fb = _font(fb.size - 2, True)
        c.text((cx, c.y + r_b), val, fb, "#FFFFFF", anchor="mm")
        c.text((cx, c.y + 2 * r_b + 36), lbl, f_badge_lbl, INK, anchor="mm")
    c.y += 2 * r_b + 90

    def card(title: str, body: list, empty: str, note: str | None = None) -> None:
        top = c.y
        inner = []
        y = top + 44
        inner.append(lambda y=y: c.text((PAD + 40, y), title, f_h))
        y += 62
        if note:
            inner.append(lambda y=y: c.text((PAD + 40, y), note, f_s, MUTED))
            y += 52
        y += 14
        if not body:
            inner.append(lambda y=y: c.text((PAD + 40, y), empty, f_b, MUTED))
            y += 60
        for draw, height in body:
            inner.append(lambda y=y, draw=draw: draw(y))
            y += height
        bottom = y + 20
        c.rrect((PAD, top, W - PAD, bottom), 44, CARD)
        for fn in inner:
            fn()
        c.y = bottom + 36

    # Numbered trades: strategy name next to every trade number
    body = []
    for r in signals:
        net = _net(r)
        good = net is not None and net > 0
        head = f"Trade #{LE.label(r)} · {r.strategy}"
        times = f"{_hm(r.entry_time)} → {_hm(r.exit_time)} · {_why(r)}"
        leg = f"{r.side} {float(r.strike):g} {r.option_type} · {_px(r.entry_fill)} → {_px(r.exit_fill)}"
        cash = _cash(r, r.lot_size, net)
        arrow, acol = ("▲ LONG", GREEN) if r.direction > 0 else ("▼ SHORT", RED)

        def draw(y, head=head, times=times, leg=leg, cash=cash, net=net, good=good, arrow=arrow,
                 acol=acol, rid=LE.label(r).rsplit("-", 1)[-1]):
            c.rrect((PAD + 40, y + 6, PAD + 128, y + 94), 22, BLUE_LIGHT)
            label = f"#{rid}"
            c.text((PAD + 84, y + 50), label, _font(30 if len(label) < 4 else 24, True), BLUE,
                   anchor="mm")
            c.text((PAD + 152, y), head, f_bb)
            c.text((PAD + 152, y + 46), times, f_s, MUTED)
            c.text((PAD + 152, y + 88), leg, f_s, MUTED)
            c.text((PAD + 152, y + 128), cash, f_s, INK)
            c.pill(W - PAD - 40, y + 20, arrow, f_s, acol, GREEN_LIGHT if acol == GREEN else RED_LIGHT)
            if net is None:
                c.pill(W - PAD - 40, y + 100, "open", f_bb, MUTED, LINE)
            else:
                c.pill(W - PAD - 40, y + 100, _rs(net, sign=True), f_bb,
                       GREEN if good else RED, GREEN_LIGHT if good else RED_LIGHT)
        body.append((draw, 186))
    card("Today's trades", body, "No numbered trades today.")

    # 9/21 EMA paper engine: same cross in 3 modes
    if ema:
        ema = sorted(ema, key=lambda r: (pd.Timestamp(r.entry_time),
                                         E.MODES.index(r.mode) if r.mode in E.MODES else 9))
        groups: dict = defaultdict(list)
        for r in ema:
            groups[r.signal_time].append(r)
        ema_nets = [n for n in (_net(r) for r in ema) if n is not None]
        body = []
        for i, group in enumerate(groups.values(), 1):
            r0 = group[0]
            arrow = "▲ LONG" if r0.direction > 0 else "▼ SHORT"
            head = f"9/21 EMA {r0.timeframe}m · signal {i} · {arrow}"
            sub = f"{_hm(r0.entry_time)} → {_hm(r0.exit_time)} · cross #{r0.cross_number} · {_why(r0)}"
            body.append((lambda y, head=head, sub=sub: (c.text((PAD + 40, y), head, f_bb),
                                                        c.text((PAD + 40, y + 46), sub, f_s, MUTED)),
                         100))
            for r in group:
                net = _net(r)
                good = net is not None and net > 0
                label = E.MODE_LABELS.get(r.mode, r.mode)
                leg = (f"{r.side} {float(r.strike):g} {r.option_type} · {_px(r.entry_fill)} → "
                       f"{_px(r.exit_fill)}")

                def draw(y, label=label, leg=leg, net=net, good=good, icon=MODE_ICON.get(r.mode, "?")):
                    c.rrect((PAD + 40, y + 6, PAD + 128, y + 94), 22, BLUE_LIGHT)
                    c.text((PAD + 84, y + 50), icon, _font(24 if len(icon) > 3 else 26, True), BLUE,
                           anchor="mm")
                    c.text((PAD + 152, y), label, f_b)
                    c.text((PAD + 152, y + 46), leg, f_s, MUTED)
                    if net is None:
                        c.pill(W - PAD - 40, y + 30, "open", f_bb, MUTED, LINE)
                    else:
                        c.pill(W - PAD - 40, y + 30, _rs(net, sign=True), f_bb,
                               GREEN if good else RED, GREEN_LIGHT if good else RED_LIGHT)
                body.append((draw, 120))
        note = (f"Same cross as EMA-921, 3 ways · subtotal {_rs(sum(ema_nets), sign=True)}"
                if ema_nets else "Same cross as EMA-921, 3 ways")
        card("9/21 EMA paper engine", body, "", note)

    top = c.y
    c.rrect((PAD, top, W - PAD, top + 150), 44, CARD)
    c.circle(PAD + 100, top + 75, 52, BLUE_LIGHT)
    c.text((PAD + 100, top + 75), "P", _font(44, True), BLUE, anchor="mm")
    c.text((PAD + 180, top + 36), "Paper trades only", f_bb)
    c.text((PAD + 180, top + 84), "No order was placed · costs NOT_VERIFIED", f_s, MUTED)
    c.y = top + 150 + PAD
    return int(c.y)


def render_png(day: date, signals: list, ema: list | None = None) -> bytes:
    """PNG bytes of the one day report: signal_trades rows (+ paper_trades rows, if any)."""
    ema = ema or []
    h = _layout(_Canvas(10, draw=False), day, signals, ema)
    c = _Canvas(h, draw=True)
    _layout(c, day, signals, ema)
    buf = io.BytesIO()
    c.img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
