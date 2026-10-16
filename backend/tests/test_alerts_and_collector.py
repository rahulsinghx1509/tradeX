import json
from datetime import UTC, datetime

import httpx
import pytest

from app.alerts.telegram import AlertError, TelegramNotifier
from app.core.market_time import IST
from app.services.collector import CollectorState, run_cycle
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
