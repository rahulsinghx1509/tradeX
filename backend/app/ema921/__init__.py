"""9/21 EMA crossover on NIFTY weekly options: backtest (and later the paper engine).

RK's rules, with every open choice settled in the rules doc
(https://claude.ai/artifact/PiDc6FGqdqS6xnc1z6sZq4). In short:

- NIFTY spot 1-minute candles are grouped into 5, 15 or 30-minute bars from the 09:15 open.
- EMA 9 and EMA 21 of the closes. A cross on a closed bar is the signal.
- Entries only when the signal bar closes inside the entry window (start / cutoff).
- Exit on the opposite cross (which also opens the reverse trade inside the window),
  otherwise square off at 15:00.
- Three ways to trade the same signals: deep ITM buy (5 strikes), ATM buy, ATM sell.
- 1 lot of 65, slippage and the full Indian cost stack, gross / friction / net PnL.

Modules:
- `bars.py`     1-minute -> 5/15/30-minute bars, EMAs, crosses
- `costs.py`    slippage and the dated regulatory cost table
- `engine.py`   signals -> trades for each mode
- `metrics.py`  win rate, profit factor, drawdown, ...
- `store.py`    reading and writing the 1-minute tables
- `loader.py`   fetching 1-minute history from Groww (read-only)
- `probe.py`    a quick check of how far back Groww's 1-minute history goes

Read-only towards Groww: nothing here places, modifies or cancels an order.
"""
