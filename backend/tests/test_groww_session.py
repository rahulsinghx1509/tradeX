"""Shared Groww login state: one login reused by every process, a shared cooldown after a
"rate limit exceeded" login (2026-11-20)."""

import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.providers import groww as G
from app.providers.base import ProviderError


class RateLimit(Exception):
    pass


RateLimit.__name__ = "GrowwAPIRateLimitException"


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "SESSION_FILE", tmp_path / ".groww_session.json")
    state = {"logins": 0, "refuse": False}

    class GrowwAPI:
        def __init__(self, token):
            self.token = token

        @staticmethod
        def get_access_token(**kw):
            state["logins"] += 1
            if state["refuse"]:
                raise RateLimit("The rate limit for the Groww API has been exceeded.")
            return f"tok{state['logins']}"

    monkeypatch.setitem(sys.modules, "growwapi", types.SimpleNamespace(GrowwAPI=GrowwAPI))
    return state


def provider():
    return G.GrowwProvider(Settings(_env_file=None, groww_api_key="k", groww_api_secret="s",
                                    groww_totp_token=None, groww_totp_secret=None))


def test_second_process_reuses_the_first_login(sdk):
    assert provider()._connect().token == "tok1"
    assert provider()._connect().token == "tok1"  # read from the shared file, no new login
    assert sdk["logins"] == 1
    assert oct(G.SESSION_FILE.stat().st_mode & 0o777) == "0o600"


def test_token_from_before_the_0600_reset_is_not_reused(sdk):
    old = (G.last_daily_reset(datetime.now(UTC)) - timedelta(minutes=1)).isoformat()
    G.write_session({"token": "stale", "logged_in_at": old})
    assert provider()._connect().token == "tok1"
    assert sdk["logins"] == 1


def test_refused_login_pauses_every_process(sdk):
    sdk["refuse"] = True
    with pytest.raises(ProviderError, match="rate limit"):
        provider()._connect()
    assert G.login_paused_for() > 100
    with pytest.raises(ProviderError, match="paused"):
        provider()._connect()  # another process: no login attempt during the cooldown
    assert sdk["logins"] == 1
    # cooldown over: the next refusal waits longer
    G.write_session({**G.read_session(), "next_login_after": 0})
    with pytest.raises(ProviderError):
        provider()._connect()
    assert G.read_session()["failures"] == 2 and G.login_paused_for() > 250
