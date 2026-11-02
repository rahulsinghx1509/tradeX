"""The end-of-day PAPER report as a phone-sized picture (PNG), sent to Telegram after the text.

Drawn with Pillow: rounded white cards on a light grey page, blue round badges for the day's
key numbers, one row per trade leg with a green (profit) or red (loss) net pill. The numbers are
the same paper_trades rows the text report uses; nothing is recomputed or estimated here.
Missing values are shown as "–", never as zero.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from app.core.market_time import IST
from app.ema921 import engine as E

W = 1080
PAD = 40
BG, CARD, INK, MUTED = "#F4F4F4", "#FFFFFF", "#1F1F1F", "#6B6B6B"
BLUE, BLUE_LIGHT = "#0B4AA2", "#E8F0FC"
GREEN, GREEN_LIGHT = "#0B7A3B", "#E3F5EA"
RED, RED_LIGHT = "#B3261E", "#FCE8E6"
LINE = "#ECECEC"

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
              "/usr/share/fonts/TTF", "/Library/Fonts", "C:/Windows/Fonts")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """DejaVu Sans (it has the ₹ sign). The server image installs fonts-dejavu-core."""
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in _FONT_DIRS:
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size)


def _rs(x, sign: bool = False) -> str:
    if x is None:
        return "–"
    v = float(x)
    s = f"₹{abs(v):,.0f}"
    if v < 0:
        return "-" + s
    return ("+" + s) if sign and v > 0 else s


def _short_rs(v: float) -> str:
    a = abs(v)
    s = f"₹{a / 1000:.1f}k" if a >= 1000 else f"₹{a:,.0f}"
    return ("-" if v < 0 else "+" if v > 0 else "") + s


def _hm(t) -> str:
    if t is None:
        return "–"
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(IST).strftime("%H:%M")


def _px(x) -> str:
    return "–" if x is None else f"{float(x):g}"


class _Canvas:
    """Draws top to bottom; height is found by a dry run first."""

    def __init__(self, height: int, draw: bool):
        self.img = Image.new("RGB", (W, max(height, 10)), BG)
        self.d = ImageDraw.Draw(self.img)
        self.draw = draw
        self.y = PAD

    def text(self, xy, s, font, fill=INK, anchor="la"):
        if self.draw:
            self.d.text(xy, s, font=font, fill=fill, anchor=anchor)

    def rrect(self, box, r, fill, outline=None):
        if self.draw:
            self.d.rounded_rectangle(box, r, fill=fill, outline=outline, width=2 if outline else 0)

    def circle(self, cx, cy, r, fill):
        if self.draw:
            self.d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)

    def width(self, s, font) -> float:
        return self.d.textlength(s, font=font)

    def pill(self, right_x, cy, s, font, fg, bg):
        w = self.width(s, font) + 44
        self.rrect((right_x - w, cy - 30, right_x, cy + 30), 30, bg, outline=None)
        self.text((right_x - w / 2, cy), s, font, fg, anchor="mm")


def _layout(c: _Canvas, day: date, tf: int, rows: list) -> int:
    f_title, f_sub = _font(50, True), _font(30)
    f_h, f_b, f_bb, f_s = _font(40, True), _font(32), _font(32, True), _font(27)
    f_badge, f_badge_lbl = _font(34, True), _font(28)

    # Header
    c.text((PAD + 8, c.y), "TradeX · Paper day report", f_title)
    c.y += 66
    c.text((PAD + 8, c.y), f"9/21 EMA {tf}m · {day:%a %d %b %Y}", f_sub, MUTED)
    c.y += 70

    rows = sorted(rows, key=lambda r: (pd.Timestamp(r.entry_time),
                                       E.MODES.index(r.mode) if r.mode in E.MODES else 9))
    by_signal: dict = defaultdict(list)
    for r in rows:
        by_signal[r.signal_time].append(r)
    by_mode: dict = defaultdict(list)
    for r in rows:
        by_mode[r.mode].append(r)
    nets = [float(r.net_pnl) for r in rows if r.net_pnl is not None]
    mode_nets = {m: sum(float(r.net_pnl) for r in rs if r.net_pnl is not None)
                 for m, rs in by_mode.items()}
    costs = sum(float(r.slippage or 0) + float(r.charges or 0) for r in rows if r.status == "CLOSED")

    # Total P&L card: every closed leg of the day added up (all modes together)
    top = c.y
    total = sum(nets) if nets else None
    gross_all = sum(float(r.gross_pnl) for r in rows if r.gross_pnl is not None)
    c.rrect((PAD, top, W - PAD, top + 230), 44, CARD)
    c.text((PAD + 40, top + 40), "Total P&L today · all modes", f_b, MUTED)
    col = INK if total is None else GREEN if total > 0 else RED if total < 0 else INK
    c.text((PAD + 40, top + 90), _rs(total, sign=True), _font(76, True), col)
    c.text((PAD + 40, top + 182), f"gross {_rs(gross_all) if rows else '–'} · "
           f"slippage + charges {_rs(costs) if rows else '–'} · {len(nets)} closed leg"
           f"{'s' if len(nets) != 1 else ''}", f_s, MUTED)
    if total is not None:
        c.pill(W - PAD - 40, top + 135, "PROFIT" if total > 0 else "LOSS" if total < 0 else "FLAT",
               f_bb, col, GREEN_LIGHT if total > 0 else RED_LIGHT if total < 0 else LINE)
    c.y = top + 230 + 44

    # Badges (like a payment app's shortcut row)
    best = max(mode_nets.values()) if mode_nets else None
    badges = [(str(len(by_signal)), "Signals"),
              (f"{sum(1 for x in nets if x > 0)}/{len(rows)}", "Winning legs"),
              (_short_rs(best) if best is not None else "–", "Best mode"),
              (_short_rs(-costs) if costs else "–", "Costs")]
    r_b, step = 66, (W - 2 * PAD) / 4
    for i, (val, lbl) in enumerate(badges):
        cx = PAD + step * i + step / 2
        c.circle(cx, c.y + r_b, r_b, BLUE)
        fb = f_badge
        while c.width(val, fb) > 2 * r_b - 16 and fb.size > 20:
            fb = _font(fb.size - 2, True)
        c.text((cx, c.y + r_b), val, fb, "#FFFFFF", anchor="mm")
        c.text((cx, c.y + 2 * r_b + 36), lbl, f_badge_lbl, INK, anchor="mm")
    c.y += 2 * r_b + 90

    # Trades card
    top = c.y
    inner = []  # drawn after the card background, so collect first

    def row(fn):
        inner.append(fn)

    y = top + 44
    row(lambda y=y: c.text((PAD + 40, y), "Today's trades", f_h))
    y += 76
    if not rows:
        row(lambda y=y: c.text((PAD + 40, y), "No paper trades today.", f_b, MUTED))
        y += 60
    for i, group in enumerate(by_signal.values(), 1):
        r0 = group[0]
        arrow = "▲ LONG" if r0.direction > 0 else "▼ SHORT"
        col = GREEN if r0.direction > 0 else RED
        why = (E.EXIT_LABELS.get(r0.exit_reason, r0.exit_reason or "–")
               if r0.exit_time is not None else "still open")
        head = f"Trade {i} · {_hm(r0.entry_time)} → {_hm(r0.exit_time)}"
        nifty = "NIFTY " + (f"{float(r0.nifty_entry):,.0f}" if r0.nifty_entry is not None else "–")
        if r0.nifty_exit is not None:
            nifty += f" → {float(r0.nifty_exit):,.0f}"
        nifty += f" · cross #{r0.cross_number}"
        row(lambda y=y, head=head: c.text((PAD + 40, y), head, f_bb))
        row(lambda y=y, arrow=arrow, col=col: c.pill(W - PAD - 40, y + 18, arrow, f_s, col,
                                                    GREEN_LIGHT if col == GREEN else RED_LIGHT))
        y += 50
        row(lambda y=y, s=f"{nifty} · {why}": c.text((PAD + 40, y), s, f_s, MUTED))
        y += 66
        for r in group:
            label = E.MODE_LABELS.get(r.mode, r.mode)
            leg = f"{r.side} {float(r.strike):g} {r.option_type} · {_px(r.entry_fill)} → {_px(r.exit_fill)}"
            net = None if r.net_pnl is None else float(r.net_pnl)
            good = net is not None and net > 0
            icon = {"ITM_BUY": "ITM", "ATM_BUY": "ATM", "ATM_SELL": "SELL"}.get(r.mode, "?")

            def draw_leg(y=y, label=label, leg=leg, net=net, good=good, icon=icon):
                c.rrect((PAD + 40, y, PAD + 128, y + 88), 22, BLUE_LIGHT)
                fi = _font(24 if len(icon) > 3 else 26, True)
                c.text((PAD + 84, y + 44), icon, fi, BLUE, anchor="mm")
                c.text((PAD + 152, y + 6), label, f_b)
                c.text((PAD + 152, y + 50), leg, f_s, MUTED)
                if net is None:
                    c.pill(W - PAD - 40, y + 44, "open", f_bb, MUTED, LINE)
                else:
                    c.pill(W - PAD - 40, y + 44, _rs(net, sign=True), f_bb,
                           GREEN if good else RED, GREEN_LIGHT if good else RED_LIGHT)
            row(draw_leg)
            y += 118
        y += 10
    bottom = y + 10
    c.rrect((PAD, top, W - PAD, bottom), 44, CARD)
    for fn in inner:
        fn()
    c.y = bottom + 36

    # Totals card
    if by_mode:
        top = c.y
        inner = []
        y = top + 44
        row(lambda y=y: c.text((PAD + 40, y), "Totals per mode", f_h))
        y += 80
        for m in E.MODES:
            rs = by_mode.get(m)
            if not rs:
                continue
            gross = sum(float(r.gross_pnl) for r in rs if r.gross_pnl is not None)
            cost = sum(float(r.slippage or 0) + float(r.charges or 0) for r in rs if r.status == "CLOSED")
            wins = sum(1 for r in rs if r.net_pnl is not None and float(r.net_pnl) > 0)
            net = mode_nets.get(m, 0.0)
            detail = (f"{len(rs)} trade{'s' if len(rs) != 1 else ''} · {wins} won · "
                      f"gross {_rs(gross)} · costs {_rs(cost)}")

            def draw_tot(y=y, m=m, detail=detail, net=net):
                c.text((PAD + 40, y), E.MODE_LABELS.get(m, m), f_b)
                c.text((PAD + 40, y + 44), detail, f_s, MUTED)
                c.text((W - PAD - 40, y + 22), _rs(net, sign=True), f_h,
                       GREEN if net > 0 else RED if net < 0 else INK, anchor="rm")
            row(draw_tot)
            y += 104
        bottom = y + 6
        c.rrect((PAD, top, W - PAD, bottom), 44, CARD)
        for fn in inner:
            fn()
        c.y = bottom + 36

    # Footer card
    top = c.y
    c.rrect((PAD, top, W - PAD, top + 150), 44, CARD)
    c.circle(PAD + 100, top + 75, 52, BLUE_LIGHT)
    c.text((PAD + 100, top + 75), "P", _font(44, True), BLUE, anchor="mm")
    c.text((PAD + 180, top + 36), "Paper trades only", f_bb)
    c.text((PAD + 180, top + 84), "No order was placed · costs NOT_VERIFIED", f_s, MUTED)
    c.y = top + 150 + PAD
    return int(c.y)


def render_png(day: date, tf: int, rows: list) -> bytes:
    """PNG bytes of the day report for these paper_trades rows."""
    h = _layout(_Canvas(10, draw=False), day, tf, rows)
    c = _Canvas(h, draw=True)
    _layout(c, day, tf, rows)
    buf = io.BytesIO()
    c.img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
