"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ActionBadge, Verdict } from "@/components/cards";
import { DecisionBody } from "@/components/decision-detail";
import { Icon } from "@/components/icons";
import { RejectionChart } from "@/components/rejection-chart";
import { Empty, Panel, Skeleton } from "@/components/ui";
import { getJson } from "@/lib/api";
import { useCockpit } from "@/lib/data";
import { actionLabel, price } from "@/lib/format";
import { dayTime } from "@/lib/time";
import type { Decision, Mode } from "@/lib/types";

const ACTIONS = ["ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "TRAIL_STOP"];

interface Filters {
  mode: Mode;
  action: string;
  approved: "" | "yes" | "no";
  from: string;
  to: string;
  minConf: string;
  maxConf: string;
}

interface Page {
  rows: Decision[];
  codes: string[];
  more: boolean;
}

function url(filters: Filters, before?: number): string {
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ""));
  if (before) query.set("before", String(before));
  return `/api/decisions?${query}`;
}

export default function DecisionsPage() {
  const { state, decisions: live } = useCockpit();
  const engineMode = state.session?.mode ?? state.heartbeat?.mode ?? "paper";
  const [filters, setFilters] = useState<Filters>({ mode: "paper", action: "", approved: "", from: "", to: "", minConf: "", maxConf: "" });
  const [rows, setRows] = useState<Decision[] | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const newest = live[0]?.id;

  useEffect(() => setFilters((f) => ({ ...f, mode: engineMode })), [engineMode]);

  const load = useCallback(async () => {
    try {
      const page = await getJson<Page>(url(filters));
      setError(null);
      setRows(page.rows);
      setMore(page.more);
      setCodes(page.codes);
    } catch (e) {
      setError(e instanceof Error ? e.message : "query failed");
    }
  }, [filters]);

  // reload when filters change, and when the engine records a new decision
  useEffect(() => void load(), [load, newest]);

  const loadMore = async () => {
    const last = rows?.at(-1);
    if (!last) return;
    try {
      const page = await getJson<Page>(url(filters, last.id));
      setRows([...(rows ?? []), ...page.rows]);
      setMore(page.more);
    } catch (e) {
      setError(e instanceof Error ? e.message : "query failed");
    }
  };

  const set = <K extends keyof Filters>(key: K, value: Filters[K]) => setFilters((f) => ({ ...f, [key]: value }));
  const field = "flex flex-col gap-1.5";

  return (
    <>
      <Panel title="Filters" icon="gauge">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <label className={field}>
            <span className="label">Account</span>
            <select className="field" value={filters.mode} onChange={(e) => set("mode", e.target.value as Mode)}>
              <option value="paper">Paper</option>
              <option value="live">Live</option>
            </select>
          </label>
          <label className={field}>
            <span className="label">Action</span>
            <select className="field" value={filters.action} onChange={(e) => set("action", e.target.value)}>
              <option value="">All</option>
              {ACTIONS.map((a) => (
                <option key={a} value={a}>
                  {actionLabel(a)}
                </option>
              ))}
            </select>
          </label>
          <label className={field}>
            <span className="label">Risk layer</span>
            <select className="field" value={filters.approved} onChange={(e) => set("approved", e.target.value as Filters["approved"])}>
              <option value="">All</option>
              <option value="yes">Approved</option>
              <option value="no">Rejected</option>
            </select>
          </label>
          <label className={field}>
            <span className="label">From</span>
            <input type="date" className="field" value={filters.from} onChange={(e) => set("from", e.target.value)} />
          </label>
          <label className={field}>
            <span className="label">To</span>
            <input type="date" className="field" value={filters.to} onChange={(e) => set("to", e.target.value)} />
          </label>
          <label className={field}>
            <span className="label">Confidence from</span>
            <input type="number" min={0} max={1} step={0.05} className="field w-28" value={filters.minConf} onChange={(e) => set("minConf", e.target.value)} />
          </label>
          <label className={field}>
            <span className="label">to</span>
            <input type="number" min={0} max={1} step={0.05} className="field w-28" value={filters.maxConf} onChange={(e) => set("maxConf", e.target.value)} />
          </label>
          <button className="btn" onClick={() => setFilters({ mode: filters.mode, action: "", approved: "", from: "", to: "", minConf: "", maxConf: "" })}>
            <Icon name="cross" size={14} /> Clear
          </button>
        </div>
      </Panel>

      <Panel title={`Why was it rejected${codes.length === 1000 ? " (latest 1,000)" : ""}`} icon="shield" delay={60}>
        <RejectionChart codes={codes} />
      </Panel>

      <Panel title="Timeline" icon="list" testId="decisions-table" delay={120}>
        {error ? (
          <p className="p-4 text-sm text-down-ink">{error}</p>
        ) : rows === null ? (
          <Skeleton lines={8} />
        ) : rows.length === 0 ? (
          <Empty>No decisions match these filters</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr>
                  <th className="th">Time (IST)</th>
                  <th className="th">Action</th>
                  <th className="th">Price</th>
                  <th className="th">Conf</th>
                  <th className="th w-full">Risk layer</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => (
                  <Fragment key={d.id}>
                    <tr className="row-hover cursor-pointer" onClick={() => setOpen(open === d.id ? null : d.id)}>
                      <td className="td num whitespace-nowrap">
                        <button aria-expanded={open === d.id} className="flex cursor-pointer items-center gap-1.5 text-left">
                          <Icon name="chevron" size={13} className={`text-faint transition-transform duration-200 ${open === d.id ? "rotate-90" : ""}`} />
                          {dayTime(d.ts)}
                        </button>
                      </td>
                      <td className="td whitespace-nowrap">
                        <span className="flex items-center gap-2">
                          <ActionBadge action={d.action} size={22} />
                          {actionLabel(d.action)}
                        </span>
                      </td>
                      <td className="td num">{price(d.price)}</td>
                      <td className="td num">{d.confidence === null ? "" : d.confidence.toFixed(2)}</td>
                      <td className="td">
                        <Verdict decision={d} compact />
                      </td>
                    </tr>
                    {open === d.id && (
                      <tr>
                        <td colSpan={5} className="border-b border-line bg-black/30 p-4">
                          <DecisionBody decision={d} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {more && (
          <div className="p-3 text-center">
            <button className="btn" onClick={() => void loadMore()}>
              Load more
            </button>
          </div>
        )}
      </Panel>
    </>
  );
}
