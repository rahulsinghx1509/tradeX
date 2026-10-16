"""Shared test setup.

By default tests use a throwaway in-memory SQLite database (nothing to install).
To run them against PostgreSQL instead, set TEST_DATABASE_URL, e.g.
    TEST_DATABASE_URL=postgresql+psycopg://tradex:change_me@localhost:5432/tradex_test
WARNING: that database is wiped by the tests. Never point it at your real database.
"""

import os
from datetime import UTC, date, datetime

# Tests always use the sample provider and default settings, whatever your .env says.
os.environ.update(DATA_PROVIDER="sample", UNDERLYINGS="NIFTY,BANKNIFTY", TIMEFRAMES="1d,1h")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_session, make_engine
from app.providers.sample import SampleProvider

# Thursday 15 Oct 2026, 16:00 IST (after the close) - a fixed "now" keeps tests repeatable.
NOW = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)
TODAY = date(2026, 10, 15)


@pytest.fixture
def engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        eng = make_engine(url)
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    with sessionmaker(bind=engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def provider():
    return SampleProvider(today=TODAY)


@pytest.fixture
def client(engine):
    from app.main import app

    maker = sessionmaker(bind=engine, expire_on_commit=False)

    def _session():
        with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    yield TestClient(app)
    app.dependency_overrides.clear()
