"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Pill, SampleBanner, StatusBadge } from "@/components/Badges";
import OverviewCard from "@/components/OverviewCard";
import { api, formatIst, type CandleResponse, type Overview, type QualityCheck } from "@/lib/api";

// The chart touches `window`, so it only renders in the browser.
const CandleChart = dynamic(() => import("@/components/CandleChart"), { ssr: false });

const TIMEFRAMES = [
  { value: "1d", label: "Daily (positional)" },
  { value: "1h", label: "1 hour" },
];
const REFRESH_MS = 60_000;

export default function Dashboard() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string>("NIFTY");
  const [instrumentId, setInstrumentId] = useState<number | null>(null);
  const [timeframe, setTimeframe] = useState("1d");
  const [candles, setCandles] = useState<CandleResponse | null>(null);
  const [checks, setChecks] = useState<QualityCheck[]>([]);

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api.overview());
      setError(null);
    } catch {
      setError("Cannot reach the TradeX API. Is the backend running on port 8000?");
    }
  }, []);

  useEffect(() => {
    loadOverview();
    const t = setInterval(loadOverview, REFRESH_MS);
    return () => clearInterval(t);
  }, [loadOverview]);

  const current = overview?.underlyings.find((u) => u.symbol === symbol);
  const contracts = useMemo(
    () => (current ? [current.index, ...current.futures].filter((x) => x !== null) : []),
    [current],
  );

  // Default to the index / stock when the underlying changes or the old contract disappears.
  useEffect(() => {
    if (contracts.length && !contracts.some((c) => c.id === instrumentId)) {
      setInstrumentId(contracts[0].id);
    }
  }, [contracts, instrumentId]);

  useEffect(() => {
    if (instrumentId === null) return;
    let cancelled = false;
    Promise.all([api.candles(instrumentId, timeframe), api.dataQuality(instrumentId)])
      .then(([c, q]) => {
        if (!cancelled) {
          setCandles(c);
          setChecks(q.checks.filter((x) => x.timeframe === timeframe));
        }
      })
      .catch(() => !cancelled && setError("Could not load candles."));
    return () => {
      cancelled = true;
    };
  }, [instrumentId, timeframe, overview?.generated_at]);

  const selected = contracts.find((c) => c.id === instrumentId);
  const latestRunChecks = checks.length ? checks.filter((c) => c.run_id === checks[0].run_id) : [];

  return (
    <main>
      {overview?.contains_sample_data && <SampleBanner />}
      <div className="mx-auto max-w-6xl space-y-6 px-4 py-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">TradeX</h1>
            <p className="text-sm text-slate-500">Analysis only. No orders are ever placed from here.</p>
          </div>
          {overview && (
            <div className="text-right text-xs text-slate-500">
              <p>Market: {overview.market_open ? "OPEN" : "CLOSED"} · Data source: {overview.data_provider}</p>
              <p>Updated {formatIst(overview.generated_at)} IST · refreshes every minute</p>
            </div>
          )}
        </header>

        {error && <p className="rounded border border-red-300 bg-red-50 p-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-200">{error}</p>}

        {overview && (
          <>
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {overview.underlyings.map((u) => (
                <OverviewCard key={u.symbol} symbol={u.symbol} index={u.index} futures={u.futures} metrics={u.metrics ?? null} />
              ))}
            </div>

            <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
              <span className="font-semibold">Signal: </span>
              <Pill tone="muted" text="NOT BUILT YET" />{" "}
              <span className="text-slate-500">{overview.signal.message}</span>
            </section>

            <section className="space-y-3 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-1">
                  Instrument
                  <select className="rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
                    value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                    {overview.underlyings.map((u) => <option key={u.symbol}>{u.symbol}</option>)}
                  </select>
                </label>
                <label className="flex items-center gap-1">
                  Contract / expiry
                  <select className="rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
                    value={instrumentId ?? ""} onChange={(e) => setInstrumentId(Number(e.target.value))}>
                    {contracts.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.instrument_type === "FUTURE"
                          ? `Future · expiry ${formatIst(c.expiry_date, false)}`
                          : `${c.symbol} ${c.instrument_type === "STOCK" ? "stock" : "index"}`}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-1">
                  Timeframe
                  <select className="rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
                    value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </label>
                {selected && <StatusBadge label="Freshness" status={selected.freshness[timeframe]?.status ?? "NO_DATA"} />}
                {candles?.is_sample && <Pill tone="warn" text="SAMPLE DATA" />}
              </div>

              {candles && candles.count > 0 ? (
                <CandleChart candles={candles.candles} showOi={selected?.instrument_type === "FUTURE"} />
              ) : (
                <p className="py-16 text-center text-sm text-slate-500">No candles stored for this selection yet.</p>
              )}
              <p className="text-xs text-slate-500">
                Times in IST. Showing the latest {candles?.count ?? 0} completed candles
                {selected?.instrument_type === "FUTURE"
                  ? "; purple line = open interest"
                  : selected?.instrument_type === "STOCK" ? "; stocks have no OI" : "; indices have no volume or OI"}.
              </p>
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
              <h2 className="mb-2 font-semibold">Data quality (latest load)</h2>
              {latestRunChecks.length ? (
                <table className="w-full text-sm">
                  <thead className="text-left text-xs uppercase text-slate-500">
                    <tr><th>Check</th><th>Status</th><th>Details</th><th>Checked (IST)</th></tr>
                  </thead>
                  <tbody>
                    {latestRunChecks.map((c) => (
                      <tr key={c.id} className="border-t border-slate-100 align-top dark:border-slate-800">
                        <td className="py-1 pr-2">{c.check_name.replaceAll("_", " ")}</td>
                        <td className="pr-2"><StatusBadge status={c.status} /></td>
                        <td className="break-all pr-2 font-mono text-xs text-slate-500">{JSON.stringify(c.details)}</td>
                        <td className="whitespace-nowrap">{formatIst(c.checked_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-sm text-slate-500">No checks recorded yet.</p>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
