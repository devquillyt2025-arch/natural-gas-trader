"use client";

import { useState } from "react";
import { Icon, type IconName } from "@/components/icons";
import { Empty, Panel, Skeleton } from "@/components/ui";
import { getJson } from "@/lib/api";
import { useCockpit } from "@/lib/data";
import { clock, dayMonth } from "@/lib/time";
import type { EventRow } from "@/lib/types";

const LEVELS = ["info", "warn", "error", "trade"] as const;
const LOOK: Record<EventRow["level"], { icon: IconName; style: string }> = {
  info: { icon: "info", style: "bg-white/[0.06] text-dim" },
  warn: { icon: "alert", style: "bg-warn/15 text-warn" },
  error: { icon: "cross", style: "bg-down/15 text-down-ink" },
  trade: { icon: "swap", style: "bg-accent/15 text-accent-ink" },
};

export default function EventsPage() {
  const { events, ready } = useCockpit();
  const [on, setOn] = useState<Set<string>>(new Set(LEVELS));
  const [older, setOlder] = useState<EventRow[]>([]);
  const [done, setDone] = useState(false);
  const all = [...events, ...older.filter((o) => !events.some((e) => e.id === o.id))];
  const shown = all.filter((e) => on.has(e.level));

  const loadOlder = async () => {
    const last = all.at(-1);
    if (!last) return;
    const rows = await getJson<EventRow[]>(`/api/events?before=${last.id}`).catch(() => null);
    if (!rows) return;
    setOlder((o) => [...o, ...rows]);
    if (rows.length < 300) setDone(true);
  };

  return (
    <Panel
      title="Engine events"
      icon="scroll"
      testId="events"
      actions={
        <div className="seg" role="group" aria-label="Filter by level">
          {LEVELS.map((level) => (
            <button
              key={level}
              aria-pressed={on.has(level)}
              className={`seg-item uppercase ${on.has(level) ? "seg-on" : ""}`}
              onClick={() => {
                const next = new Set(on);
                if (next.has(level)) next.delete(level);
                else next.add(level);
                setOn(next);
              }}
            >
              {level}
            </button>
          ))}
        </div>
      }
    >
      {!ready ? (
        <Skeleton lines={8} />
      ) : shown.length === 0 ? (
        <Empty icon="scroll">No events</Empty>
      ) : (
        <ul>
          {shown.map((e) => (
            <li key={e.id} className="row-hover grid grid-cols-[auto_minmax(0,1fr)] items-start gap-3 border-b border-line px-4 py-2.5 last:border-0 md:grid-cols-[auto_8.5rem_minmax(0,1fr)]">
              <span className={`inline-flex h-7 w-7 items-center justify-center rounded-lg ${LOOK[e.level].style}`} title={e.level}>
                <Icon name={LOOK[e.level].icon} size={14} />
                <span className="sr-only">{e.level}</span>
              </span>
              <span className="num hidden pt-1 text-xs text-dim md:block">
                {dayMonth(e.ts)} {clock(Date.parse(e.ts))}
              </span>
              <span className="min-w-0 break-words pt-0.5 text-sm">
                <span className="num block text-xxs text-faint md:hidden">
                  {dayMonth(e.ts)} {clock(Date.parse(e.ts))}
                </span>
                {e.msg}
              </span>
            </li>
          ))}
        </ul>
      )}
      {ready && all.length >= 300 && !done && (
        <div className="p-3 text-center">
          <button className="btn" onClick={() => void loadOlder()}>
            Load older
          </button>
        </div>
      )}
    </Panel>
  );
}
