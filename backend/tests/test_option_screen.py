"""Option screen OS-1.0: finds nothing in noise, finds a planted link, never leaks."""

import numpy as np
import pandas as pd

from app.services import feature_screen as fs
from app.services import option_screen as osc
from tests.null_world import world


def test_random_world_has_no_survivors():
    res = osc.screen(osc.dev_only(world(7)))
    assert not res["survives"].any()
    # IV metrics against volatility targets are flagged, not scored as edges
    mech = res[res["mechanical"]]
    assert set(mech["feature"]) <= set(osc.IV_FAMILY)


def test_planted_skew_link_is_found():
    t = world(8)
    # plant: high skew today -> lower 20-day return, across underlyings
    noise = np.random.default_rng(0).normal(0, 1, len(t))
    t["ret_20d_fwd"] = -2.0 * (t["iv_skew"] - t.groupby(level=0)["iv_skew"].transform("mean")) + noise
    res = osc.screen(osc.dev_only(t))
    hit = res[(res.feature == "iv_skew") & (res.target == "ret_20d_fwd")].iloc[0]
    assert hit["survives"] and hit["ic"] < 0


def test_dev_only_cuts_forward_windows_before_validation():
    dev = osc.dev_only(world(9))
    assert dev.index.max() < pd.Timestamp(fs.VALIDATION_FROM)
    last = dev[dev.symbol == "NIFTY"].tail(fs.VOL_WINDOW + 1)
    assert last["rv_vs_iv"].isna().all() and last["vol_surprise"].isna().all()


def test_report_lists_premium_table():
    dev = osc.dev_only(world(10))
    md = osc.to_markdown(dev, osc.screen(dev), "now")
    assert "Survivors" in md and "Were options usually overpriced" in md and "| NIFTY |" in md
