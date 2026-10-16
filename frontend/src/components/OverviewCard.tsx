import { StatusBadge } from "@/components/Badges";
import { formatIst, formatNumber, type InstrumentSummary } from "@/lib/api";

export default function OverviewCard({ symbol, index, futures }: {
  symbol: string;
  index: InstrumentSummary | null;
  futures: InstrumentSummary[];
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
