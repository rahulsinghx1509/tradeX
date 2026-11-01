"""Open the 9/21 EMA trade viewer: filters, KPIs, trade list, candle chart, cost card.

Reads the stored 1-minute data (load it first with ema921_load.py). Needs no Groww access and
cannot place orders: every page and API call only reads.

Usage (from the repository root):
    python scripts/ema921_viewer.py                 # http://127.0.0.1:8050
    python scripts/ema921_viewer.py --years 3
On the server it runs by itself as the "ema" service: http://<server>/ema/
"""

import argparse
import sys
from datetime import date

import _bootstrap  # noqa: F401
from waitress import serve

from app.db.session import get_sessionmaker
from app.ema921 import loader as L
from app.ema921 import web


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--years", type=int, default=2)
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    a = p.parse_args()
    start, end = L.default_range(L.ist_today(), a.years)
    start = date.fromisoformat(a.start) if a.start else start
    end = date.fromisoformat(a.end) if a.end else end
    app = web.create_app(web.Backtests(get_sessionmaker(), start, end))
    print(f"9/21 EMA viewer for {start} to {end} on http://{a.host}:{a.port}/")
    serve(app, host=a.host, port=a.port, threads=4)
    return 0


if __name__ == "__main__":
    sys.exit(main())
