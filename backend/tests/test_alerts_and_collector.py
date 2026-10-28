import json
from datetime import UTC, date, datetime

import httpx
import pytest

from app.alerts.telegram import AlertError, ConsoleNotifier, TelegramNotifier, get_notifier
from app.core.config import Settings
from app.core.market_time import IST
from app.db.models import Instrument
from app.services.collector import CollectorState, run_cycle, track_problems
from app.services.ingestion import sync_instruments

TOKEN = "123:fake-token-for-tests"


def notifier_with(handler):
    return TelegramNotifier(TOKEN, "42", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_telegram_send_posts_message():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    notifier_with(handler).send("hello")
    assert seen["url"].endswith("/sendMessage") and seen["body"] == {
        "chat_id": "42", "text": "hello", "disable_web_page_preview": True}


def test_telegram_error_does_not_leak_token():
    n = notifier_with(lambda r: httpx.Response(401, json={"ok": False, "description": "Unauthorized"}))
    with pytest.raises(AlertError) as exc:
        n.send("x")
    assert "Unauthorized" in str(exc.value) and TOKEN not in str(exc.value)


def test_telegram_sends_to_every_chat_and_reports_failures():
    sent = []

    def handler(request):
        chat = json.loads(request.content)["chat_id"]
        sent.append(chat)
        if chat == "222":
            return httpx.Response(400, json={"ok": False, "description": "chat not found"})
        return httpx.Response(200, json={"ok": True})

    n = TelegramNotifier(TOKEN, " 111, 222 ,333", client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AlertError, match="chat 2 of 3: .*chat not found"):
        n.send("hello")
    assert sent == ["111", "222", "333"]  # one bad chat doesn't stop the others


def test_notifier_needs_a_chat_id():
    s = Settings(telegram_bot_token="x", telegram_chat_id=" , ")
    assert isinstance(get_notifier(s), ConsoleNotifier)
    s = Settings(telegram_bot_token="x", telegram_chat_id="1,2")
    assert isinstance(get_notifier(s), TelegramNotifier)


def test_telegram_chat_id_lookup():
    updates = {"ok": True, "result": [{"message": {"chat": {"id": 7, "type": "private",
                                                            "first_name": "RK"}}}]}
    n = notifier_with(lambda r: httpx.Response(200, json=updates))
    assert n.recent_chat_ids() == [{"chat_id": 7, "type": "private", "name": "RK"}]


class Recorder:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)


def ist(d, hh, mm):
    return datetime(2026, 10, d, hh, mm, tzinfo=IST).astimezone(UTC)


def test_collector_intraday_then_end_of_day(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    rec, state = Recorder(), CollectorState()

    assert run_cycle(session, provider, rec, state, ist(15, 11, 0)) == "intraday"
    assert run_cycle(session, provider, rec, state, ist(15, 11, 5)) == "intraday"
    assert sum("collector is running" in m for m in rec.messages) == 1  # one heartbeat per day

    assert run_cycle(session, provider, rec, state, ist(15, 15, 45)) == "end_of_day"
    assert run_cycle(session, provider, rec, state, ist(15, 15, 50)) == "idle"  # only once
    assert any("end of day" in m for m in rec.messages)


def test_collector_sleeps_on_weekend(session, provider):
    assert run_cycle(session, provider, Recorder(), CollectorState(), ist(17, 11, 0)) == "idle"


def test_data_problems_alert_only_when_they_last():
    state = CollectorState()
    assert track_problems(state, {"NSE-LT-FUT quote": "timeout"}) == []  # first time: wait
    assert track_problems(state, {}) == [] and state.blips == 1  # fixed itself: a blip only
    track_problems(state, {"X 1h candles": "rate limit"})
    msgs = track_problems(state, {"X 1h candles": "rate limit"})
    assert len(msgs) == 1 and "X 1h candles (rate limit)" in msgs[0]
    assert track_problems(state, {"X 1h candles": "rate limit"}) == []  # sent once, not every cycle
    assert track_problems(state, {}) == ["TradeX data recovered: X 1h candles."]
    assert state.blips == 1


def test_collector_refreshes_contracts_each_morning(session, provider):
    sync_instruments(session, provider, ["NIFTY"], 1)
    expired = Instrument(symbol="NIFTY", instrument_type="FUTURE", exchange="NSE", segment="FNO",
                         expiry_date=date(2026, 9, 29), lot_size=65, provider=provider.name,
                         provider_symbol="SAMPLE-NSE-NIFTY-29Sep26-FUT", is_sample=True)
    session.add(expired)
    session.commit()
    rec = Recorder()
    run_cycle(session, provider, rec, CollectorState(), ist(15, 11, 0))
    assert not expired.is_active  # an expired contract stops being polled (no daily false alarms)
    assert not any("data problem" in m for m in rec.messages)
