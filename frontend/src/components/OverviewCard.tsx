import { StatusBadge } from "@/components/Badges";
import { formatIst, formatNumber, type CoreMetrics, type HourlyMetrics, type InstrumentSummary, type Reading } from "@/lib/api";

const READINGS: [string, string][] = [
  ["D1_daily_trend", "Daily trend"],
  ["D2_weekly_trend", "Weekly trend"],
  ["D3_trend_strength", "Trend strength"],
  ["D4_momentum", "Momentum"],
];

const HOURLY_READINGS: [string, string][] = [
  ["H1_hourly_trend", "Hourly trend"],
  ["H2_hourly_momentum", "Hourly momentum"],
  ["H3_hourly_pullback", "Hourly pullback"],
];

const READING_STYLE: Record<Reading, string> = {
  UP: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  DOWN: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  NEUTRAL: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  MISSING: "bg-amber-50 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
};

function Metric({ label, value, digits = 2 }: { label: string; value: number | null | undefined; digits?: number }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-slate-500">{label}</dt>
      <dd className="tabular-nums">{value === null || value === undefined ? "missing" : formatNumber(value, digits)}</dd>
    </div>
  );
}

function ReadingChips({ items, readings }: { items: [string, string][]; readings: Record<string, Reading> }) {
  return (
    <div className="flex flex-wrap gap-1">
      {items.map(([key, label]) => {
        const r = readings[key] ?? "MISSING";
        return (
          <span key={key} className={`rounded px-1.5 py-0.5 text-xs font-medium ${READING_STYLE[r]}`}>
            {label}: {r.toLowerCase()}
          </span>
        );
      })}
    </div>
  );
}

function HourlyPanel({ hourly }: { hourly: HourlyMetrics }) {
  const v = hourly.values;
  return (
    <div className="mt-3 border-t border-slate-100 pt-3 dark:border-slate-800">
      <ReadingChips items={HOURLY_READINGS} readings={hourly.readings} />
      {hourly.status === "OK" && (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
          <Metric label="1h EMA 20" value={v.ema20} />
          <Metric label="1h EMA 50" value={v.ema50} />
          <Metric label="1h RSI 14" value={v.rsi14} digits={1} />
          <Metric label="1h from EMA20 (ATRs)" value={v.ema20_distance_atr} />
        </dl>
      )}
      <p className="mt-2 text-xs text-slate-500">
        From {hourly.bars} hourly candles up to the one starting {hourly.as_of ?? "—"} IST. Updates after each completed hour. Information only, not a trade signal.
      </p>
      {hourly.notes.map((n) => (
        <p key={n} className="text-xs text-amber-700 dark:text-amber-400">{n}</p>
      ))}
    </div>
  );
}

function MetricsPanel({ metrics }: { metrics: CoreMetrics }) {
  const v = metrics.values;
  return (
    <div className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800">
      <ReadingChips items={READINGS} readings={metrics.readings} />
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs">
        <Metric label="EMA 20" value={v.ema20} />
        <Metric label="EMA 50" value={v.ema50} />
        <Metric label="EMA 200" value={v.ema200} />
        <Metric label="Weekly EMA 20" value={v.weekly_ema20} />
        <Metric label="ADX 14" value={v.adx14} digits={1} />
        <Metric label="RSI 14" value={v.rsi14} digits={1} />
        <Metric label="ATR 14" value={v.atr14} />
        <Metric label="ATR % of price" value={v.atr_pct} />
        <Metric label="From EMA20 (ATRs)" value={v.ema20_distance_atr} />
        <Metric label="RS 20d vs NIFTY" value={v.rs20_vs_nifty} digits={3} />
      </dl>
      <p className="mt-2 text-xs text-slate-500">
        From {metrics.bars} daily candles up to {formatIst(metrics.as_of, false)}. Information only, not a trade signal.
      </p>
      {metrics.notes.map((n) => (
        <p key={n} className="text-xs text-amber-700 dark:text-amber-400">{n}</p>
      ))}
    </div>
  );
}

export default function OverviewCard({ symbol, index, futures, metrics, hourly }: {
  symbol: string;
  index: InstrumentSummary | null;
  futures: InstrumentSummary[];
  metrics: CoreMetrics | null;
  hourly: HourlyMetrics | null;
}) {
  const change = index?.change ?? null;
  const up = change !== null && change >= 0;
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">{symbol}</h2>
        {index && <StatusBadge label="Quality" status={index.data_quality} />}
      </div>
      {index ? (
        <>
          <p className="mt-2 text-3xl font-bold tabular-nums">{formatNumber(index.latest_price ?? index.last_close)}</p>
          <p className={`text-sm tabular-nums ${change === null ? "text-slate-500" : up ? "text-emerald-600" : "text-red-600"}`}>
            {change === null ? "No previous close" : `${up ? "+" : ""}${formatNumber(change)} (${formatNumber(index.change_pct)}%) vs previous day close`}
          </p>
          <p className="mt-1 text-xs text-slate-500">Latest 1h candle: {formatIst(index.latest_price_time)} IST</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {Object.entries(index.freshness).map(([tf, f]) => (
              <StatusBadge key={tf} label={tf} status={f.status} />
            ))}
          </div>
        </>
      ) : (
        <p className="mt-2 text-sm text-slate-500">No data loaded yet. Run scripts/backfill_candles.py.</p>
      )}
      {metrics && metrics.status !== "NO_DATA" && <MetricsPanel metrics={metrics} />}
      {hourly && hourly.status !== "NO_DATA" && <HourlyPanel hourly={hourly} />}
      {futures.length > 0 && (
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs uppercase text-slate-500">
            <tr><th>Future</th><th className="text-right">Price</th><th className="text-right">OI</th><th className="text-right">DTE</th><th className="text-right">Lot</th></tr>
          </thead>
          <tbody className="tabular-nums">
            {futures.map((f) => (
              <tr key={f.id} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-1">{formatIst(f.expiry_date, false)}</td>
                <td className="text-right">{formatNumber(f.latest_price ?? f.last_close)}</td>
                <td className="text-right">{formatNumber(f.open_interest, 0)}</td>
                <td className="text-right">{f.days_to_expiry ?? "—"}</td>
                <td className="text-right">{f.lot_size ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
