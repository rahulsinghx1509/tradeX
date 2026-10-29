import type { VixWarning } from "@/lib/api";

// VJ-1.0: the only rule that passed a held-back test. A volatility warning, never a buy/sell signal.
// Its banner is about option selling, so it is hidden since the F&O view was switched off
// (2026-10-29); the Telegram alert still runs. Import it in app/page.tsx to switch it back on.
export default function VixWarningBanner({ w }: { w: VixWarning }) {
  const tone =
    w.status === "WARNING"
      ? "border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100"
      : w.status === "NO_DATA"
        ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
        : "border-slate-200 bg-white text-slate-600 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300";
  const title =
    w.status === "WARNING" ? "Volatility warning: option selling NO TRADE"
      : w.status === "NO_DATA" ? "Volatility warning unavailable: option selling NO TRADE"
        : "Volatility warning: clear";
  return (
    <section className={`rounded-lg border p-4 text-sm ${tone}`}>
      <p className="font-semibold">{title}</p>
      <p>{w.message}</p>
      <p className="mt-1 text-xs opacity-75">
        Rule {w.version}{w.as_of ? ` · India VIX close of ${w.as_of}` : ""}
        {w.vix !== null ? ` · VIX ${w.vix.toFixed(2)}` : ""}
      </p>
    </section>
  );
}
