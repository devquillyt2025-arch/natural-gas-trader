"use client";

import { useState } from "react";
import { pnl, toneClass } from "@/lib/format";
import { dailyPnl } from "@/lib/stats";
import type { Trade } from "@/lib/types";
import { Empty } from "./ui";

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
const MAX_MONTHS = 4;
const NONE = "rgba(255,255,255,0.045)";

/** Diverging scale: two hues for the sign, strength for the size, neutral for no trades. */
function cellColor(value: number | undefined, max: number): string {
  if (value === undefined) return NONE;
  if (value === 0) return "rgba(163,173,194,0.25)";
  const strength = 0.28 + 0.64 * Math.min(1, Math.abs(value) / max);
  return value > 0 ? `rgba(0,184,148,${strength})` : `rgba(245,69,92,${strength})`;
}

/** Monday-first weeks of a month, weekdays only (MCX does not trade weekends). */
function weeks(month: string): (string | null)[][] {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const rows: (string | null)[][] = [];
  let row: (string | null)[] = [];
  const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
  for (let d = 1; d <= days; d++) {
    const weekday = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
    if (weekday > 4) continue;
    if (row.length === 0) row = Array<null>(weekday).fill(null);
    row.push(`${month}-${String(d).padStart(2, "0")}`);
    if (weekday === 4) {
      rows.push(row);
      row = [];
    }
  }
  if (row.length) rows.push([...row, ...Array<null>(5 - row.length).fill(null)]);
  return rows;
}

export function PnlCalendar({ trades }: { trades: Trade[] }) {
  const [asTable, setAsTable] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);
  const days = dailyPnl(trades);
  if (days.size === 0) return <Empty icon="calendar">No trades yet</Empty>;
  const max = Math.max(1, ...[...days.values()].map(Math.abs));
  const months = [...new Set([...days.keys()].map((d) => d.slice(0, 7)))].sort().slice(-MAX_MONTHS);
  const counts = new Map<string, number>();
  for (const t of trades) {
    const day = new Date(Date.parse(t.closed_at) + 5.5 * 3_600_000).toISOString().slice(0, 10);
    counts.set(day, (counts.get(day) ?? 0) + 1);
  }

  return (
    <div className="p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="num text-xs text-dim" aria-live="polite">
          {focus && days.has(focus) ? (
            <>
              {focus}: <span className={toneClass(days.get(focus))}>{pnl(days.get(focus))}</span> from {counts.get(focus)} trade(s)
            </>
          ) : (
            "Hover or focus a day"
          )}
        </p>
        <button className="btn" aria-pressed={asTable} onClick={() => setAsTable(!asTable)}>
          {asTable ? "Show calendar" : "Show as table"}
        </button>
      </div>
      {asTable ? (
        <table className="w-full max-w-sm">
          <thead>
            <tr>
              <th className="th">Day</th>
              <th className="th">Trades</th>
              <th className="th">P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {[...days].reverse().map(([day, value]) => (
              <tr key={day}>
                <td className="td num">{day}</td>
                <td className="td num">{counts.get(day)}</td>
                <td className={`td num ${toneClass(value)}`}>{pnl(value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="flex flex-wrap gap-6">
          {months.map((month) => (
            <div key={month}>
              <h3 className="label mb-2">{MONTH.format(Date.parse(`${month}-01T00:00:00Z`))}</h3>
              <div className="grid grid-cols-5 gap-1">
                {["Mo", "Tu", "We", "Th", "Fr"].map((d) => (
                  <span key={d} className="w-10 text-center text-xxs text-faint">
                    {d}
                  </span>
                ))}
                {weeks(month)
                  .flat()
                  .map((day, i) =>
                    day === null ? (
                      <span key={`blank-${i}`} />
                    ) : (
                      <button
                        key={day}
                        className="num h-9 w-10 cursor-pointer rounded-lg border border-white/5 text-xxs text-ink transition-transform duration-150 hover:scale-110"
                        style={{ background: cellColor(days.get(day), max) }}
                        aria-label={days.has(day) ? `${day}: ${pnl(days.get(day))}` : `${day}: no trades`}
                        title={days.has(day) ? `${day}  ${pnl(days.get(day))}` : `${day}  no trades`}
                        onMouseEnter={() => setFocus(day)}
                        onFocus={() => setFocus(day)}
                        onClick={() => setFocus(day)}
                      >
                        <span className={days.has(day) ? "font-semibold" : "text-faint"}>{Number(day.slice(8))}</span>
                      </button>
                    ),
                  )}
              </div>
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 flex flex-wrap items-center gap-3 text-xxs text-dim">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded" style={{ background: "rgba(245,69,92,0.9)" }} /> loss {pnl(-max)}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded" style={{ background: NONE }} /> no trades
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-3 w-3 rounded" style={{ background: "rgba(0,184,148,0.9)" }} /> profit {pnl(max)}
        </span>
      </p>
    </div>
  );
}
