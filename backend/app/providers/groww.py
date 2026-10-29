"""Groww Trade API adapter (real market data).

Built from Groww's official Python SDK documentation (`growwapi` package):
  https://groww.in/trade-api/docs/python-sdk
  https://groww.in/trade-api/docs/python-sdk/backtesting   (historical candles)
  https://groww.in/trade-api/docs/python-sdk/live-data     (quotes)

Everything here follows the documentation, but NONE of it has been tested on the
owner's account yet, so every capability is NOT_VERIFIED. Run
`python scripts/verify_provider_capabilities.py` once credentials are in `.env`.

Milestone 0 on the owner's account (2026-10-17) confirmed: login with TOTP, NIFTY and
BANKNIFTY ("NSE-BANKNIFTY") daily and hourly candles, history back to 2020, index quotes
and India VIX ("INDIAVIX"). Hourly candles come stamped 09:00, 10:00, ... (clock hours),
so the first one of the day is pinned to the 09:15 open.

Still to verify (the verify script checks each one):
- Candle timestamps without a timezone are Indian time (IST).
- The instrument CSV marks futures with instrument_type "FUT" and indices "IDX".
- Stock candles use groww_symbol "NSE-<SYMBOL>" in the CASH segment.

This adapter only READS data. It never calls any order, modify or cancel method.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.config import Settings
from app.core.market_time import IST, bar_starts_for_day, session_bounds, to_ist
from app.providers.base import (
    Candle,
    Capability,
    InstrumentInfo,
    MarketDataProvider,
    ProviderError,
    Snapshot,
)

log = logging.getLogger(__name__)

# Values from the SDK "Annexures" page.
INTERVALS = {"1h": "1hour", "1d": "1day", "15m": "15minute", "5m": "5minute"}
# Max time range per candle request (from the backtesting docs): 1h / 1d -> 180 days.
# The minute windows are NOT_VERIFIED and kept short on purpose: a short window only costs
# extra calls, a too-long one fails. `tradex.sh intraday-probe` shows the real limits.
MAX_WINDOW = {"1h": timedelta(days=180), "1d": timedelta(days=180),
              "15m": timedelta(days=14), "5m": timedelta(days=7)}
MIN_SECONDS_BETWEEN_CALLS = 0.15  # stays under the documented 10 requests/second
# Waits before retrying a call Groww refused with "rate limit" (seconds).
RATE_LIMIT_BACKOFF = (2, 4, 8, 16)
# NSE indices with F&O. Anything else in UNDERLYINGS is treated as a stock.
KNOWN_INDICES = frozenset({"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"})
# Groww resets every login session daily at 06:00 IST, so log in again after that.
DAILY_RESET_HOUR_IST = 6


def is_rate_limited(exc: Exception) -> bool:
    return type(exc).__name__ == "GrowwAPIRateLimitException" or "rate limit" in str(exc).lower()


def is_auth_expired(exc: Exception) -> bool:
    return type(exc).__name__ == "GrowwAPIAuthenticationException"


def last_daily_reset(now: datetime) -> datetime:
    """The most recent 06:00 IST at or before `now`."""
    ist = to_ist(now)
    reset = ist.replace(hour=DAILY_RESET_HOUR_IST, minute=0, second=0, microsecond=0)
    if reset > ist:
        reset -= timedelta(days=1)
    return reset.astimezone(UTC)


def parse_candle_time(raw: Any) -> datetime:
    """Groww candle timestamps: 'yyyy-MM-dd HH:mm:ss', ISO with 'T', or epoch seconds."""
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(raw, tz=UTC)
    text = str(raw).replace("T", " ")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)  # ASSUMPTION (NOT_VERIFIED): naive = IST
    return dt.astimezone(UTC)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    v = _num(value)
    return None if v is None else int(v)


def parse_candles(payload: dict, timeframe: str) -> list[Candle]:
    """Turn a get_historical_candles response into Candle objects.

    Each candle is [timestamp, open, high, low, close, volume, open_interest].
    Missing values stay None. Daily candles are pinned to 09:15 IST of their day, and
    the first hourly candle (stamped 09:00 by Groww) is pinned to the 09:15 open.
    """
    out = []
    for row in payload.get("candles") or []:
        row = list(row) + [None] * (7 - len(row))
        t = parse_candle_time(row[0])
        if timeframe == "1d":
            t = bar_starts_for_day(to_ist(t).date(), "1d")[0]
        elif timeframe == "1h":
            t = max(t, session_bounds(to_ist(t).date())[0])
        out.append(Candle(t, _num(row[1]), _num(row[2]), _num(row[3]), _num(row[4]),
                          _int(row[5]), _int(row[6])))
    return out


def parse_quote(payload: dict, received_at: datetime) -> Snapshot:
    ohlc = payload.get("ohlc") or {}
    ltt = payload.get("last_trade_time")  # epoch milliseconds per the docs
    observed = datetime.fromtimestamp(ltt / 1000, tz=UTC) if ltt else received_at
    return Snapshot(
        observed_at=observed,
        last_price=_num(payload.get("last_price")),
        bid_price=_num(payload.get("bid_price")),
        ask_price=_num(payload.get("offer_price")),
        day_open=_num(ohlc.get("open")),
        day_high=_num(ohlc.get("high")),
        day_low=_num(ohlc.get("low")),
        previous_close=_num(ohlc.get("close")),
        volume=_int(payload.get("volume")),
        open_interest=_int(payload.get("open_interest")),
        oi_change=_int(payload.get("oi_day_change")),
        implied_volatility=_num(payload.get("implied_volatility")),
    )


def spot_instrument(sym: str, cash: Any, has_futures: bool) -> InstrumentInfo | None:
    """The index or stock itself (cash segment) for one underlying, or None if Groww has no such symbol.

    Uses the NSE cash row from the instrument list when there is one. Otherwise falls back to
    "NSE-<symbol>" (confirmed for NIFTY and BANKNIFTY by Milestone 0) for known indices, and
    for stocks that have futures. ASSUMPTION (NOT_VERIFIED): the list marks indices "IDX".
    """
    rows = cash[cash["trading_symbol"] == sym] if len(cash) else cash
    if len(rows):
        row = rows.iloc[0]
        kind = "INDEX" if str(row.get("instrument_type")) == "IDX" or sym in KNOWN_INDICES else "STOCK"
        provider_symbol = str(row.get("groww_symbol") or f"NSE-{sym}")
    elif sym in KNOWN_INDICES:
        kind, provider_symbol = "INDEX", f"NSE-{sym}"
    elif has_futures:
        kind, provider_symbol = "STOCK", f"NSE-{sym}"
    else:
        return None
    return InstrumentInfo(symbol=sym, instrument_type=kind, exchange="NSE", segment="CASH",
                          provider_symbol=provider_symbol, trading_symbol=sym)


class GrowwProvider(MarketDataProvider):
    name = "groww"
    is_sample = False

    def __init__(self, settings: Settings, client: Any | None = None):
        self._settings = settings
        self._client = client
        self._logged_in_at: datetime | None = None  # None for an injected (test) client
        self._last_call = 0.0

    # ------------------------------------------------------------ connection
    def _connect(self) -> Any:
        if self._client is not None and not self._session_expired():
            return self._client
        self._client = None
        s = self._settings
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("growwapi is not installed: pip install -r requirements.txt") from exc
        try:
            if s.groww_totp_token and s.groww_totp_secret:
                import pyotp

                totp = pyotp.TOTP(s.groww_totp_secret.get_secret_value()).now()
                token = GrowwAPI.get_access_token(
                    api_key=s.groww_totp_token.get_secret_value(), totp=totp
                )
            elif s.groww_api_key and s.groww_api_secret:
                token = GrowwAPI.get_access_token(
                    api_key=s.groww_api_key.get_secret_value(),
                    secret=s.groww_api_secret.get_secret_value(),
                )
            else:
                raise ProviderError(
                    "No Groww credentials. Set GROWW_API_KEY + GROWW_API_SECRET "
                    "(or GROWW_TOTP_TOKEN + GROWW_TOTP_SECRET) in .env"
                )
            self._client = GrowwAPI(token)
            self._logged_in_at = datetime.now(UTC)
        except ProviderError:
            raise
        except Exception as exc:
            # Only the error type is reported, so a secret can never leak into logs.
            raise ProviderError(f"Groww login failed ({type(exc).__name__}: {exc})") from None
        return self._client

    def _session_expired(self) -> bool:
        """True once Groww's daily 06:00 IST reset has passed since we logged in."""
        if self._logged_in_at is None:
            return False
        return self._logged_in_at < last_daily_reset(datetime.now(UTC))

    def _call(self, method: str, **kwargs: Any) -> Any:
        """Call one SDK method: spaced out, retried on rate limits, re-logged-in once if expired."""
        backoff = iter(RATE_LIMIT_BACKOFF)
        relogged = False
        while True:
            client = self._connect()
            wait = MIN_SECONDS_BETWEEN_CALLS - (_time.monotonic() - self._last_call)
            if wait > 0:
                _time.sleep(wait)
            try:
                return getattr(client, method)(**kwargs)
            except Exception as exc:
                if is_rate_limited(exc) and (delay := next(backoff, None)) is not None:
                    _time.sleep(delay)
                    continue
                if is_auth_expired(exc) and not relogged and self._logged_in_at is not None:
                    self._client, relogged = None, True
                    continue
                raise ProviderError(f"Groww {method} failed ({type(exc).__name__}: {exc})") from None
            finally:
                self._last_call = _time.monotonic()

    def client(self) -> Any:
        """The SDK client with this adapter's spacing, retries and re-login on every call."""
        return _ManagedClient(self)

    # ------------------------------------------------------------ interface
    def list_instruments(self, underlyings: list[str], futures_expiries: int) -> list[InstrumentInfo]:
        import pandas as pd

        df = self._call("get_all_instruments")
        if df is None or len(df) == 0:
            raise ProviderError("Groww instrument list is empty")
        df = df.copy()
        df["_expiry"] = pd.to_datetime(df["expiry_date"], errors="coerce").dt.date
        today = to_ist(datetime.now(UTC)).date()
        futs = df[
            (df["exchange"] == "NSE")
            & (df["segment"] == "FNO")
            & (df["instrument_type"] == "FUT")
            & (df["underlying_symbol"].isin(underlyings))
            & (df["_expiry"] >= today)
        ].sort_values("_expiry")
        cash = df[(df["exchange"] == "NSE") & (df["segment"] == "CASH")]

        out = []
        for sym in underlyings:
            sym_futs = futs[futs["underlying_symbol"] == sym]
            spot = spot_instrument(sym, cash, has_futures=len(sym_futs) > 0)
            if spot is None:
                # Unknown or renamed symbol: skip it rather than fail every 5 minutes.
                log.warning("%s is not in Groww's NSE instrument list; not tracking it", sym)
                continue
            out.append(spot)
            for _, row in sym_futs.head(futures_expiries).iterrows():
                out.append(
                    InstrumentInfo(
                        symbol=sym, instrument_type="FUTURE", exchange="NSE", segment="FNO",
                        provider_symbol=str(row["groww_symbol"]),
                        trading_symbol=str(row["trading_symbol"]),
                        expiry_date=row["_expiry"],
                        lot_size=_int(row.get("lot_size")),
                    )
                )
        return out

    def expired_future(self, underlying: str, year: int, month: int) -> InstrumentInfo | None:
        """The futures contract that expired in this month, or None if Groww lists none.

        Uses the documented get_expiries / get_contracts calls. Expiries are tried from the last
        one in the month backwards, because the monthly expiry is usually the last (weekly index
        expiries have options only). Lot size is not returned for expired contracts: left empty.
        """
        exp = (self._call("get_expiries", exchange="NSE", underlying_symbol=underlying,
                          year=year, month=month) or {}).get("expiries") or []
        for e in sorted((str(x)[:10] for x in exp), reverse=True):
            contracts = (self._call("get_contracts", exchange="NSE", underlying_symbol=underlying,
                                    expiry_date=e) or {}).get("contracts") or []
            futures = [c for c in contracts if str(c).endswith("FUT")]
            if futures:
                return InstrumentInfo(symbol=underlying, instrument_type="FUTURE", exchange="NSE",
                                      segment="FNO", provider_symbol=str(futures[0]),
                                      expiry_date=date.fromisoformat(e))
        return None

    def upcoming_expiries(self, underlying: str, today: date, count: int) -> list[date]:
        """The next `count` option expiries (today included), from get_expiries."""
        exp = (self._call("get_expiries", exchange="NSE", underlying_symbol=underlying)
               or {}).get("expiries") or []
        days = sorted({date.fromisoformat(str(e)[:10]) for e in exp})
        return [d for d in days if d >= today][:count]

    def option_chain(self, underlying: str, expiry: date) -> dict:
        """The raw get_option_chain response for one expiry (field meanings NOT_VERIFIED)."""
        return self._call("get_option_chain", exchange="NSE", underlying=underlying,
                          expiry_date=expiry.isoformat()) or {}

    def get_candles(
        self, instrument: InstrumentInfo, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        if timeframe not in INTERVALS:
            raise ProviderError(f"Unsupported timeframe {timeframe}")
        candles: list[Candle] = []
        window_start = start
        while window_start <= end:
            window_end = min(window_start + MAX_WINDOW[timeframe], end)
            payload = self._call(
                "get_historical_candles",
                exchange=instrument.exchange,
                segment=instrument.segment,
                groww_symbol=instrument.provider_symbol,
                start_time=to_ist(window_start).strftime("%Y-%m-%d %H:%M:%S"),
                end_time=to_ist(window_end).strftime("%Y-%m-%d %H:%M:%S"),
                candle_interval=INTERVALS[timeframe],
                timeout=30,
            )
            candles.extend(parse_candles(payload or {}, timeframe))
            window_start = window_end + timedelta(seconds=1)
        return [c for c in candles if start <= c.time <= end]

    def get_snapshot(self, instrument: InstrumentInfo) -> Snapshot | None:
        payload = self._call(
            "get_quote",
            exchange=instrument.exchange,
            segment=instrument.segment,
            trading_symbol=instrument.trading_symbol or instrument.symbol,
        )
        if not payload:
            return None
        return parse_quote(payload, datetime.now(UTC))

    def capabilities(self) -> dict[str, Capability]:
        # Stays NOT_VERIFIED until scripts/verify_provider_capabilities.py proves otherwise.
        return {
            "historical_candles_1d": Capability.NOT_VERIFIED,
            "historical_candles_1h": Capability.NOT_VERIFIED,
            "futures_open_interest": Capability.NOT_VERIFIED,
            "quotes": Capability.NOT_VERIFIED,
            "option_chain": Capability.NOT_VERIFIED,
        }


class _ManagedClient:
    """Looks like the raw SDK client, but every method goes through GrowwProvider._call."""

    def __init__(self, provider: GrowwProvider):
        self._provider = provider

    def __getattr__(self, method: str) -> Any:
        return lambda **kwargs: self._provider._call(method, **kwargs)
