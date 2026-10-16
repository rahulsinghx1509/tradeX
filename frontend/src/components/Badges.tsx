import type { FreshnessStatus } from "@/lib/api";

const tone: Record<string, string> = {
  FRESH: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  PASS: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  STALE: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  WARN: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  FAIL: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  NO_DATA: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

export function StatusBadge({ label, status }: { label?: string; status: FreshnessStatus | string }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${tone[status] ?? tone.NO_DATA}`}>
      {label ? `${label}: ` : ""}
      {status.replace("_", " ")}
    </span>
  );
}

export function SampleBanner() {
  return (
    <div className="border-b border-amber-300 bg-amber-100 px-4 py-2 text-center text-sm font-semibold text-amber-900 dark:border-amber-700 dark:bg-amber-900/60 dark:text-amber-100">
      SAMPLE DATA: these prices are synthetic, generated for testing. They are not real market prices.
    </div>
  );
}

export function Pill({ text, tone: t }: { text: string; tone: "warn" | "muted" }) {
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${t === "warn" ? tone.WARN : tone.NO_DATA}`}>
      {text}
    </span>
  );
}
