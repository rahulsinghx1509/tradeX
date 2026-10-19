"""NSE participant OI file: parsing, loading, readings, and screen PS-1.0 on random worlds."""

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from app.db.models import ParticipantOi
from app.services import feature_screen as fs
from app.services import participant_oi as po
from app.services import participant_screen as ps
from tests.null_world import _ar1, world

# Layout as NSE publishes it (title line, then header); NOT_VERIFIED against a real server file.
HEADER = ",".join(["Client Type", "Future Index Long", "Future Index Short", "Future Stock Long",
                    "Future Stock Short\t", "Option Index Call Long", "Option Index Put Long",
                    "Option Index Call Short", "Option Index Put Short", "Option Stock Call Long",
                    "Option Stock Put Long", "Option Stock Call Short", "Option Stock Put Short",
                    "Total Long Contracts\t", "Total Short Contracts"])
SAMPLE = f"""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Oct 16, 2026
{HEADER}
Client,"150,000",50000,2000000,300000,900000,800000,700000,600000,100,200,300,400,5000000,4000000
DII,10000,90000,10000,2000000,0,10,0,0,0,0,0,0,30000,2100000
FII,30000,170000,1000000,900000,300000,350000,250000,200000,10,20,30,40,2000000,1500000
Pro,60000,40000,300000,100000,500000,600000,800000,900000,1,2,3,4,1500000,2000000
TOTAL,250000,350000,3310000,3300000,1700000,1750010,1750000,1700000,111,222,333,444,8530000,9600000
"""
D = date(2026, 10, 16)
NO_HOLIDAYS: frozenset[date] = frozenset()


def test_parse_matches_columns_by_name():
    rows = {r["client_type"]: r for r in po.parse(SAMPLE)}
    assert set(rows) == {"Client", "DII", "FII", "Pro", "TOTAL"}
    assert rows["Client"]["fut_idx_long"] == 150_000  # thousands separator handled
    assert rows["FII"]["fut_idx_short"] == 170_000 and rows["FII"]["total_short"] == 1_500_000


def test_format_change_fails_loudly():
    broken = SAMPLE.replace("Future Index Long", "Fut Idx L")
    with pytest.raises(ValueError, match="fut_idx_long"):
        po.parse(broken)
    with pytest.raises(ValueError, match="Client Type"):
        po.parse("<html>maintenance</html>")


def client_for(files: dict[str, str]) -> httpx.Client:
    def handler(r):
        body = files.get(str(r.url))
        return httpx.Response(200, content=body.encode()) if body else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_load_stores_and_skips(session):
    d2 = date(2026, 10, 19)
    res = po.load(session, client_for({po.url(D): SAMPLE}), D, d2, pause=0, holidays=NO_HOLIDAYS)
    assert [r.status for r in res] == ["LOADED", "NO_FILE"]
    assert session.scalar(select(func.count()).select_from(ParticipantOi)) == 5
    again = po.load(session, client_for({}), D, D, pause=0, holidays=NO_HOLIDAYS)
    assert again[0].status == "SKIPPED"
    assert po.latest_day(session) == D


def test_readings(session):
    po.load(session, client_for({po.url(D): SAMPLE}), D, D, pause=0, holidays=NO_HOLIDAYS)
    r = po.features(po.load_frame(session)).iloc[0]
    assert r["fii_fut_long_share"] == pytest.approx(30_000 / 200_000)
    assert r["client_fut_long_share"] == pytest.approx(0.75)
    # FII options: (300k − 250k) − (350k − 200k) = −100k over 1.1M
    assert r["fii_opt_bias"] == pytest.approx(-100_000 / 1_100_000)
    assert pd.isna(r["fii_fut_long_share_chg_5d"])  # no 5-day history: missing, not zero


def _random_table(seed: int) -> pd.DataFrame:
    w = world(seed)
    base = w[w.symbol == "NIFTY"]
    rng = np.random.default_rng(1000 + seed)
    rd = pd.DataFrame(index=base.index)
    for f in ps.FEATURES:
        rd[f] = _ar1(rng, len(base), 0.97, 1.0)
    rd["fii_fut_long_share_chg_5d"] = rd["fii_fut_long_share"] - rd["fii_fut_long_share"].shift(5)
    return ps.build_table(base, rd)


def test_random_world_has_no_survivors():
    res = ps.screen(fs.dev_only(_random_table(3)))
    assert (res["test"] == "market").all() and not res["survives"].any()


def test_planted_link_is_found():
    t = _random_table(4)
    noise = np.random.default_rng(0).normal(0, 1, len(t))
    t["ret_20d_fwd"] = 3.0 * t["fii_fut_long_share"] + noise
    res = ps.screen(fs.dev_only(t))
    hit = res[(res.feature == "fii_fut_long_share") & (res.target == "ret_20d_fwd")].iloc[0]
    assert hit["survives"] and hit["ic"] > 0


def test_report_renders():
    dev = fs.dev_only(_random_table(5))
    md = ps.to_markdown(dev, ps.screen(dev), "now", 1300)
    assert "Survivors" in md and "fii_opt_bias" in md and "PS-1.0" in md
