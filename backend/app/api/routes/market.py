from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serialize import instrument_dict, num, ts
from app.core.config import get_settings
from app.core.market_time import from_db, is_market_open, to_ist, utc_now
from app.db.models import DataQualityLog, IngestionRun, Instrument, MarketCandle, MarketSnapshot
from app.db.session import get_session
from app.providers.factory import get_provider
from app.services import metrics as core_metrics
from app.services import vix_warning
from app.services.freshness import candle_freshness

router = APIRouter(prefix="/api")
SEVERITY = {"PASS": 0, "WARN": 1, "FAIL": 2}
LIVE_MAX_AGE_MINUTES = 15  # the collector takes a quote every ~5 minutes


def _last_two_closes(session: Session, instrument_id: int) -> list[MarketCandle]:
    q = (select(MarketCandle)
         .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == "1d",
                MarketCandle.quality_status == "VALID")
         .order_by(MarketCandle.candle_time.desc()).limit(2))
    return list(session.scalars(q))


def _latest_hourly(session: Session, instrument_id: int) -> MarketCandle | None:
    return session.scalar(
        select(MarketCandle)
        .where(MarketCandle.instrument_id == instrument_id, MarketCandle.timeframe == "1h",
               MarketCandle.quality_status == "VALID")
        .order_by(MarketCandle.candle_time.desc()).limit(1))


def _live_quote(session: Session, instrument_id: int, now) -> dict:
    """Today's latest 5-minute quote, for information only: readings use completed candles."""
    snap = session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.instrument_id == instrument_id, MarketSnapshot.last_price.is_not(None))
        .order_by(MarketSnapshot.observed_at.desc()).limit(1))
    if snap is None or to_ist(from_db(snap.observed_at)).date() != to_ist(now).date():
        return {"live_price": None, "live_time": None, "live_status": "NO_DATA"}
    age_min = (now - from_db(snap.observed_at)).total_seconds() / 60
    fresh = not snap.is_stale and age_min <= LIVE_MAX_AGE_MINUTES
    return {"live_price": num(snap.last_price), "live_time": ts(snap.observed_at),
            "live_status": "LIVE" if fresh else "OLD"}


def _latest_dq_status(session: Session, instrument_id: int) -> str:
    """Worst check result from the most recent candle run of this instrument."""
    last_run = session.scalar(
        select(func.max(IngestionRun.id)).where(IngestionRun.instrument_id == instrument_id,
                                                IngestionRun.job == "candles"))
    if last_run is None:
        return "NO_DATA"
    statuses = session.scalars(select(DataQualityLog.status).where(DataQualityLog.run_id == last_run))
    return max(statuses, key=lambda s: SEVERITY.get(s, 0), default="NO_DATA")


def _instrument_summary(session: Session, inst: Instrument, now) -> dict:
    closes = _last_two_closes(session, inst.id)
    last = closes[0] if closes else None
    prev = closes[1] if len(closes) > 1 else None
    hourly = _latest_hourly(session, inst.id)
    # Day change: today's latest hourly price vs the last completed daily close, or,
    # before today's first hourly candle, the last daily close vs the one before it.
    hourly_is_newer = bool(hourly and last and to_ist(from_db(hourly.candle_time)).date()
                           > to_ist(from_db(last.candle_time)).date())
    current, reference = (hourly, last) if hourly_is_newer else (last, prev)
    change = (num(current.close) - num(reference.close)) if (current and reference) else None
    out = instrument_dict(inst) | {
        # Latest completed 1-hour candle: the most recent price we trust during the day.
        "latest_price": num(hourly.close) if hourly else None,
        "latest_price_time": ts(hourly.candle_time) if hourly else None,
        "last_close": num(last.close) if last else None,
        "last_close_time": ts(last.candle_time) if last else None,
        "previous_close": num(prev.close) if prev else None,
        "change": round(change, 2) if change is not None else None,
        "change_pct": round(change / num(reference.close) * 100, 2) if change is not None else None,
        "change_reference_close": num(reference.close) if reference else None,
        "open_interest": last.open_interest if last else None,
        "freshness": {tf: asdict(candle_freshness(session, inst.id, tf, now))
                      for tf in get_settings().timeframe_list},
        "data_quality": _latest_dq_status(session, inst.id),
    } | _live_quote(session, inst.id, now)
    for f in out["freshness"].values():
        f["last_candle_time"] = ts(f["last_candle_time"])
        f["expected_latest"] = ts(f["expected_latest"])
    if inst.expiry_date:
        out["days_to_expiry"] = (inst.expiry_date - to_ist(now).date()).days
    return out


@router.get("/market/overview")
def overview(session: Session = Depends(get_session)) -> dict:
    now = utc_now()
    settings = get_settings()
    underlyings = []
    spots: list[Instrument] = []
    for sym in settings.underlying_list:
        insts = list(session.scalars(
            select(Instrument).where(Instrument.symbol == sym, Instrument.is_active,
                                     Instrument.provider == settings.data_provider.lower())
            .order_by(Instrument.instrument_type.desc(), Instrument.expiry_date)))
        # "index" = the underlying itself: the index, or the stock's cash price.
        index = next((i for i in insts if i.instrument_type in ("INDEX", "STOCK")), None)
        futures = [i for i in insts if i.instrument_type == "FUTURE"]
        if index:
            spots.append(index)
        underlyings.append({
            "symbol": sym,
            "index": _instrument_summary(session, index, now) if index else None,
            "futures": [_instrument_summary(session, f, now) for f in futures],
            "_spot_id": index.id if index else None,
        })
    # Core metrics (Milestone 2) on the underlying's own daily candles. Information only.
    computed = core_metrics.compute_for_instruments(session, spots, now)
    for u in underlyings:
        spot_id = u.pop("_spot_id")
        m = computed.get(spot_id)
        u["metrics"] = (asdict(m) | {"as_of": m.as_of.isoformat() if m.as_of else None}) if m else None
        # Hourly readings (H1-H3) on the underlying's completed 1h candles. Information only.
        hourly = (core_metrics.hourly_frame(session, spot_id, core_metrics.DASHBOARD_HOURLY_BARS)
                  if spot_id else None)
        u["hourly"] = asdict(core_metrics.compute_hourly(hourly)) if hourly is not None else None
    any_sample = any((u["index"] or {}).get("is_sample") for u in underlyings)
    return {
        "generated_at": now.isoformat(),
        "market_open": is_market_open(now),
        "data_provider": settings.data_provider,
        "contains_sample_data": any_sample,
        "metrics_version": core_metrics.METRICS_VERSION,
        "hourly_version": core_metrics.HOURLY_VERSION,
        # VJ-1.0: the only tested rule. A volatility warning (option selling NO TRADE), not a signal.
        "vix_warning": vix_warning.current(session, settings.data_provider.lower(), now).to_dict(),
        "signal": {"status": "NOT_BUILT",
                   "message": "Trade signals are not built yet (rule engine comes after "
                              "core metrics and backtesting)."},
        "underlyings": underlyings,
    }


@router.get("/capabilities")
def capabilities() -> dict:
    """What the configured provider can do, as far as it has been verified."""
    provider = get_provider(get_settings())
    return {"provider": provider.name, "is_sample": provider.is_sample,
            "capabilities": {k: v.value for k, v in provider.capabilities().items()}}
