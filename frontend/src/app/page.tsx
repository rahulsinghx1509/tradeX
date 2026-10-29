"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Pill, SampleBanner, StatusBadge } from "@/components/Badges";
import OverviewCard from "@/components/OverviewCard";
import {
  api, formatIst, formatNumber, type CandleResponse, type IntradayResponse, type Overview, type QualityCheck,
} from "@/lib/api";

// The chart touches `window`, so it only renders in the browser.
const CandleChart = dynamic(() => import("@/components/CandleChart"), { ssr: false });

// Intraday first. "Auto" shows the smallest size that has candles stored (5-min data starts once
// the collector gathers it; until then the chart falls back to 15-min, then 1 hour).
const TIMEFRAMES = [
  { value: "auto", label: "Auto (smallest available)" },
  { value: "5m", label: "5 min" },
  { value: "15m", label: "15 min" },
  { value: "1h", label: "1 hour" },
  { value: "1d", label: "Daily" },
];
const AUTO_ORDER = ["5m", "15m", "1h"];

function formatRange(low: number | null | undefined, high: number | null | undefined): string {
  return low === null || low === undefined || high === null || high === undefined
    ? "—" : `${formatNumber(low)}–${formatNumber(high)}`;
}
const INTRADAY = new Set(["5m", "15m", "1h"]);
const REFRESH_MS = 60_000;

export default function Dashboard() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [symbol, setSymbol] = useState<string>("NIFTY");
  const [instrumentId, setInstrumentId] = useState<number | null>(null);
  const [timeframe, setTimeframe] = useState("auto");
  const [candles, setCandles] = useState<CandleResponse | null>(null);
  const [intraday, setIntraday] = useState<IntradayResponse | null>(null);
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
  // F&O view switched off 2026-10-29 (RK): only the stock / index itself, no futures contracts.
  // const contracts = useMemo(
  //   () => (current ? [current.index, ...current.futures].filter((x) => x !== null) : []),
  //   [current],
  // );
  const contracts = useMemo(() => (current?.index ? [current.index] : []), [current]);

  // Default to the index / stock when the underlying changes or the old contract disappears.
  useEffect(() => {
    if (contracts.length && !contracts.some((c) => c.id === instrumentId)) {
      setInstrumentId(contracts[0].id);
    }
  }, [contracts, instrumentId]);

  useEffect(() => {
    if (instrumentId === null) return;
    let cancelled = false;
    const load = async () => {
      // Auto: try 5m, then 15m, then 1h, and keep the first size that has candles.
      let tf = timeframe;
      let c: CandleResponse | null = null;
      for (const t of timeframe === "auto" ? AUTO_ORDER : [timeframe]) {
        c = await api.candles(instrumentId, t, t === "1h" ? 60 : 300);
        tf = t;
        if (c.count > 0) break;
      }
      const [q, iday] = await Promise.all([
        api.dataQuality(instrumentId),
        INTRADAY.has(tf) ? api.intraday(instrumentId, tf, tf === "1h" ? 60 : 300) : Promise.resolve(null),
      ]);
      if (!cancelled) {
        setCandles(c);
        setIntraday(iday);
        setChecks(q.checks.filter((x) => x.timeframe === tf));
      }
    };
    load().catch(() => !cancelled && setError("Could not load candles."));
    return () => {
      cancelled = true;
    };
  }, [instrumentId, timeframe, overview?.generated_at]);

  const selected = contracts.find((c) => c.id === instrumentId);
  const shownTf = candles?.timeframe ?? timeframe;
  const lv = intraday?.levels;
  const lastBar = intraday?.bars.length ? intraday.bars[intraday.bars.length - 1] : null;
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
          // The chart comes first (order-first) since this page is mainly for intraday reading.
          <div className="flex flex-col gap-6">
            {/* F&O view switched off 2026-10-29 (RK). Banner kept in components/VixWarningBanner.tsx:
            {overview.vix_warning && <VixWarningBanner w={overview.vix_warning} />} */}
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {overview.underlyings.map((u) => (
                <OverviewCard key={u.symbol} symbol={u.symbol} index={u.index} metrics={u.metrics ?? null} hourly={u.hourly ?? null} />
              ))}
            </div>

            <section className="rounded-lg border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
              <span className="font-semibold">Signal: </span>
              <Pill tone="muted" text="NOT BUILT YET" />{" "}
              <span className="text-slate-500">{overview.signal.message}</span>
            </section>

            <section className="order-first space-y-3 rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
              <div className="flex flex-wrap items-center gap-3 text-sm">
                <label className="flex items-center gap-1">
                  Instrument
                  <select className="rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
                    value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                    {overview.underlyings.map((u) => <option key={u.symbol}>{u.symbol}</option>)}
                  </select>
                </label>
                {/* F&O view switched off 2026-10-29 (RK). Contract / expiry picker:
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
                */}
                <label className="flex items-center gap-1">
                  Timeframe
                  <select className="rounded border border-slate-300 bg-transparent px-2 py-1 dark:border-slate-700"
                    value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                    {TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </label>
                {selected?.freshness[shownTf] && <StatusBadge label="Freshness" status={selected.freshness[shownTf].status} />}
                {candles?.is_sample && <Pill tone="warn" text="SAMPLE DATA" />}
              </div>

              {intraday?.session && (
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums text-slate-600 dark:text-slate-300">
                  <span>Day {intraday.session}</span>
                  <span>PDH {formatNumber(lv?.prev_high)}</span>
                  <span>PDL {formatNumber(lv?.prev_low)}</span>
                  <span>PDC {formatNumber(lv?.prev_close)}</span>
                  <span>Gap {lv?.gap_pct === null || lv?.gap_pct === undefined ? "—" : `${formatNumber(lv.gap_pct)}%`}</span>
                  <span>Pivot {formatNumber(lv?.pivot)} (R1 {formatNumber(lv?.r1)} · S1 {formatNumber(lv?.s1)})</span>
                  <span>CPR {formatRange(lv?.cpr_bottom, lv?.cpr_top)}</span>
                  <span>Opening range {formatRange(lastBar?.or_low, lastBar?.or_high)}</span>
                  <span>VWAP {formatNumber(lastBar?.vwap)}</span>
                  <span>RVOL {formatNumber(lastBar?.rvol)}</span>
                </div>
              )}

              {candles && candles.count > 0 ? (
                <CandleChart candles={candles.candles} intraday={intraday} />
              ) : (
                <p className="py-16 text-center text-sm text-slate-500">No candles stored for this selection yet.</p>
              )}
              <p className="text-xs text-slate-500">
                Times in IST. Showing the latest {candles?.count ?? 0} completed {shownTf} candles
                {INTRADAY.has(shownTf) ? ". Orange = VWAP, blue dashed = previous day high/low, purple = opening range (first 15 min)" : ""}
                {selected?.instrument_type === "INDEX" ? "; indices have no volume, so no VWAP" : ""}. Readings only, not signals.
              </p>
            </section>

            <details className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
              <summary className="cursor-pointer font-semibold">Data quality (latest load)</summary>
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
            </details>
          </div>
        )}
      </div>
    </main>
  );
}
