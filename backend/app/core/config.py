"""Application settings.

Settings are read from environment variables, or from the `.env` file in the
repository root. Copy `.env.example` to `.env` and fill it in. `.env` is in
`.gitignore`, so secrets never reach GitHub.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Database -----------------------------------------------------------
    database_url: str = "postgresql+psycopg://tradex:change_me@localhost:5432/tradex"

    # --- Market data provider ----------------------------------------------
    # "sample" = clearly labelled synthetic data (no account needed).
    # "groww"  = real data from the Groww Trade API (needs credentials below).
    data_provider: str = "sample"

    # Groww offers two ways to log in. Fill in ONE of them.
    # 1) API key + secret (needs a daily approval on the Groww API keys page)
    groww_api_key: SecretStr | None = None
    groww_api_secret: SecretStr | None = None
    # 2) TOTP token + TOTP secret (no daily approval)
    groww_totp_token: SecretStr | None = None
    groww_totp_secret: SecretStr | None = None

    # --- What to collect -----------------------------------------------------
    # The 12-instrument pool from docs/01_DECISIONS_AND_CONTEXT.md, with FINNIFTY in place of
    # TATAMOTORS (demerged in 2025). Symbols missing from Groww's list are skipped with a warning.
    underlyings: str = (
        "NIFTY,BANKNIFTY,FINNIFTY,HDFCBANK,ICICIBANK,SBIN,AXISBANK,INFY,TCS,RELIANCE,BHARTIARTL,LT"
    )
    timeframes: str = "1d,1h"
    # How far back the backfill goes. Groww says FNO/index history starts in 2020.
    backfill_days_1d: int = Field(default=1825, ge=1)
    backfill_days_1h: int = Field(default=365, ge=1)
    # How many upcoming futures expiries to track per underlying.
    futures_expiries_to_track: int = Field(default=2, ge=1, le=3)

    # --- Alerts (Telegram) ---------------------------------------------------
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    # --- Collector (the always-on process) -----------------------------------
    collector_interval_minutes: int = Field(default=5, ge=1, le=60)

    # --- API -------------------------------------------------------------------
    # Comma separated list of websites allowed to call the API from a browser.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def underlying_list(self) -> list[str]:
        return [s.strip().upper() for s in self.underlyings.split(",") if s.strip()]

    @property
    def timeframe_list(self) -> list[str]:
        return [s.strip() for s in self.timeframes.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [s.strip() for s in self.cors_origins.split(",") if s.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """Accept plain `postgresql://` URLs and use the psycopg 3 driver for them."""
        url = self.database_url
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://") :]
        elif url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://") :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
