// Typed helpers for the TradeX read-only API (backend/app/api/routes).

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export type FreshnessStatus = "FRESH" | "STALE" | "NO_DATA";

export interface Freshness {
  status: FreshnessStatus;
  last_candle_time: string | null;
  expected_latest: string | null;
  lag_minutes: number | null;
}

export interface Instrument {
  id: number;
  symbol: string;
  instrument_type: "INDEX" | "STOCK" | "FUTURE" | "OPTION";
  exchange: string;
  segment: string;
  expiry_date: string | null;
  lot_size: number | null;
  provider: string;
  provider_symbol: string;
  trading_symbol: string | null;
  is_sample: boolean;
  is_active: boolean;
}

export interface InstrumentSummary extends Instrument {
  latest_price: number | null;
  latest_price_time: string | null;
  last_close: number | null;
  last_close_time: string | null;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  open_interest: number | null;
  freshness: Record<string, Freshness>;
  data_quality: "PASS" | "WARN" | "FAIL" | "NO_DATA";
  days_to_expiry?: number;
}

export type Reading = "UP" | "DOWN" | "NEUTRAL" | "MISSING";

// Core metrics on the underlying's own daily candles (backend/app/services/metrics.py).
// Information only: these are not trade signals.
export interface CoreMetrics {
  as_of: string | null;
  bars: number;
  status: "OK" | "INSUFFICIENT_HISTORY" | "NO_DATA";
  values: Record<string, number | null>;
  readings: Record<string, Reading>;
  notes: string[];
}

// Hourly readings on the underlying's completed 1h candles (metrics.py, compute_hourly).
// Refreshed after every completed hour during market hours. Information only.
export interface HourlyMetrics {
  as_of: string | null; // IST start time of the last 1h candle, "YYYY-MM-DD HH:MM"
  bars: number;
  status: "OK" | "INSUFFICIENT_HISTORY" | "NO_DATA";
  values: Record<string, number | null>;
  readings: Record<string, Reading>;
  notes: string[];
}

export interface VixWarning {
  version: string;
  status: "WARNING" | "CLEAR" | "NO_DATA";
  as_of: string | null;
  vix: number | null;
  change_5d_pct: number | null;
  jump_pct: number;
  started_today: boolean;
  message: string;
}

export interface Overview {
  generated_at: string;
  market_open: boolean;
  data_provider: string;
  contains_sample_data: boolean;
  metrics_version: string;
  hourly_version?: string;
  vix_warning?: VixWarning;
  signal: { status: string; message: string };
  underlyings: {
    symbol: string;
    index: InstrumentSummary | null;
    futures: InstrumentSummary[];
    metrics: CoreMetrics | null;
    hourly?: HourlyMetrics | null;
  }[];
}

export interface Candle {
  time: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
  open_interest: number | null;
  quality_status: "VALID" | "INVALID";
  quality_reason: string | null;
}

export interface CandleResponse {
  instrument: Instrument;
  timeframe: string;
  is_sample: boolean;
  count: number;
  candles: Candle[];
}

export interface QualityCheck {
  id: number;
  run_id: number | null;
  instrument_id: number | null;
  timeframe: string | null;
  check_name: string;
  status: "PASS" | "WARN" | "FAIL";
  details: Record<string, unknown> | null;
  checked_at: string;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API ${path} returned ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  overview: () => get<Overview>("/api/market/overview"),
  candles: (instrumentId: number, timeframe: string, limit = 500) =>
    get<CandleResponse>(`/api/candles?instrument_id=${instrumentId}&timeframe=${timeframe}&limit=${limit}`),
  dataQuality: (instrumentId: number) =>
    get<{ checks: QualityCheck[] }>(`/api/data-quality?instrument_id=${instrumentId}&limit=30`),
};

const IST = "Asia/Kolkata";

export function formatIst(iso: string | null, withTime = true): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", {
    timeZone: IST,
    day: "2-digit",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  });
}

export function formatNumber(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "—"; // missing stays missing, never 0
  return v.toLocaleString("en-IN", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}
