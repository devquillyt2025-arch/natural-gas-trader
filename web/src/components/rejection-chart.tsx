"use client";

import { reasonLabel } from "@/lib/format";
import { Empty } from "./ui";

/** One measure (count) across categories: sorted horizontal bars in a single hue, labelled directly. */
export function RejectionChart({ codes }: { codes: string[] }) {
  const counts = new Map<string, number>();
  for (const code of codes) counts.set(code, (counts.get(code) ?? 0) + 1);
  const bars = [...counts].sort((a, b) => b[1] - a[1]);
  if (bars.length === 0) return <Empty icon="check">No rejections in this selection</Empty>;
  const max = bars[0]![1];
  const total = codes.length;
  return (
    <ul className="space-y-2 p-4" data-testid="rejection-chart">
      {bars.map(([code, count]) => (
        <li key={code} className="grid grid-cols-[minmax(8rem,13rem)_1fr_4.5rem] items-center gap-3 text-xs" title={`${reasonLabel(code)}: ${count} of ${total} rejections`}>
          <span className="truncate text-dim">{reasonLabel(code)}</span>
          <span className="h-2.5 rounded-full bg-white/[0.04]">
            <span className="block h-2.5 rounded-full bg-gradient-to-r from-accent/60 to-accent-ink transition-[width] duration-700 ease-out" style={{ width: `${(count / max) * 100}%`, minWidth: 4 }} />
          </span>
          <span className="num text-right">
            {count} <span className="text-faint">{Math.round((count / total) * 100)}%</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
