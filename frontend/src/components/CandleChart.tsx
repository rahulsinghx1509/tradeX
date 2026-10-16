"use client";

import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { Candle } from "@/lib/api";

// The chart library has no timezone setting, so we shift UTC by +5:30
// to make the time axis read in Indian Standard Time.
const IST_OFFSET_SECONDS = 5.5 * 60 * 60;
const toChartTime = (iso: string) => (Date.parse(iso) / 1000 + IST_OFFSET_SECONDS) as UTCTimestamp;

export default function CandleChart({ candles, showOi }: { candles: Candle[]; showOi: boolean }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const chart: IChartApi = createChart(ref.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: dark ? "#cbd5e1" : "#334155",
      },
      grid: {
        vertLines: { color: dark ? "#1e293b" : "#e2e8f0" },
        horzLines: { color: dark ? "#1e293b" : "#e2e8f0" },
      },
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    // Only valid candles with all four prices are drawn; invalid ones are listed in the quality table.
    const valid = candles.filter(
      (c) => c.quality_status === "VALID" && c.open !== null && c.high !== null && c.low !== null && c.close !== null,
    );
    chart.addSeries(CandlestickSeries, {
      upColor: "#16a34a", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#16a34a", wickDownColor: "#dc2626",
    }).setData(
      valid.map((c) => ({ time: toChartTime(c.time), open: c.open!, high: c.high!, low: c.low!, close: c.close! })),
    );

    // Volume as bars along the bottom (skipped where volume is missing, never drawn as 0).
    const volume = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(
      valid.filter((c) => c.volume !== null).map((c) => ({
        time: toChartTime(c.time), value: c.volume!,
        color: c.close! >= c.open! ? "rgba(22,163,74,0.35)" : "rgba(220,38,38,0.35)",
      })),
    );

    if (showOi) {
      const oi = chart.addSeries(LineSeries, { priceScaleId: "oi", color: "#6366f1", lineWidth: 2, title: "OI" });
      chart.priceScale("oi").applyOptions({ scaleMargins: { top: 0.6, bottom: 0.2 } });
      oi.setData(
        valid.filter((c) => c.open_interest !== null).map((c) => ({ time: toChartTime(c.time), value: c.open_interest! })),
      );
    }

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, showOi]);

  return <div ref={ref} className="h-[420px] w-full" />;
}
