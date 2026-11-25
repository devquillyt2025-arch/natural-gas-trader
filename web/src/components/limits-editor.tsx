"use client";

import { useState } from "react";
import type { CommandType, LimitsState, LimitValues } from "@/lib/types";
import { CommandStatus, ConfirmDialog, useCommand } from "./commands";
import { Empty } from "./ui";

const FIELDS: { key: keyof LimitValues; label: string; step: number; lowerIsTighter: boolean }[] = [
  { key: "max_lots", label: "Max lots", step: 1, lowerIsTighter: true },
  { key: "max_risk_inr", label: "Max risk per trade (₹)", step: 100, lowerIsTighter: true },
  { key: "daily_loss_cap_inr", label: "Daily loss cap (₹)", step: 100, lowerIsTighter: true },
  { key: "max_trades_per_day", label: "Max trades per day", step: 1, lowerIsTighter: true },
  { key: "min_rr", label: "Min reward:risk", step: 0.1, lowerIsTighter: false },
  { key: "min_sl_atr", label: "Min stop distance (× ATR)", step: 0.1, lowerIsTighter: false },
  { key: "max_sl_atr", label: "Max stop distance (× ATR)", step: 0.1, lowerIsTighter: true },
  { key: "min_confidence", label: "Min confidence", step: 0.05, lowerIsTighter: false },
  { key: "cooldown_min_after_loss", label: "Cooldown after a loss (min)", step: 5, lowerIsTighter: false },
  { key: "eia_before_min", label: "EIA blackout before (min)", step: 5, lowerIsTighter: false },
  { key: "eia_after_min", label: "EIA blackout after (min)", step: 5, lowerIsTighter: false },
];

/** Changed values only. */
export function limitChanges(limits: LimitsState, edits: Partial<Record<keyof LimitValues, string>>): Record<string, number> {
  const changes: Record<string, number> = {};
  for (const [key, raw] of Object.entries(edits) as [keyof LimitValues, string][]) {
    const value = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(value) && value !== limits.effective[key]) changes[key] = value;
  }
  return changes;
}

export function LimitsEditor({ limits }: { limits: LimitsState | null }) {
  const [edits, setEdits] = useState<Partial<Record<keyof LimitValues, string>>>({});
  const [confirm, setConfirm] = useState(false);
  const { send, command, error, pending } = useCommand();
  if (!limits) return <Empty icon="shield">The engine has not published its limits yet</Empty>;
  const changes = limitChanges(limits, edits);
  const count = Object.keys(changes).length;

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              <th className="th">Hard limit</th>
              <th className="th">Now</th>
              <th className="th">Loosest allowed</th>
              <th className="th">New value</th>
            </tr>
          </thead>
          <tbody>
            {FIELDS.map((f) => {
              const raw = edits[f.key];
              const next = raw === undefined || raw === "" ? null : Number(raw);
              const beyond = next !== null && (f.lowerIsTighter ? next > limits.ceilings[f.key] : next < limits.ceilings[f.key]);
              return (
                <tr key={f.key} className="row-hover">
                  <td className="td">
                    <label htmlFor={`limit-${f.key}`}>{f.label}</label>
                  </td>
                  <td className="td num font-semibold">{limits.effective[f.key]}</td>
                  <td className="td num text-dim">
                    {f.lowerIsTighter ? "≤" : "≥"} {limits.ceilings[f.key]}
                  </td>
                  <td className="td">
                    <input
                      id={`limit-${f.key}`}
                      type="number"
                      inputMode="decimal"
                      step={f.step}
                      className={`field num w-28 ${beyond ? "border-warn" : ""}`}
                      value={raw ?? ""}
                      placeholder={String(limits.effective[f.key])}
                      onChange={(e) => setEdits({ ...edits, [f.key]: e.target.value })}
                    />
                    {beyond && <p className="text-xxs text-warn">beyond the ceiling: will be rejected</p>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-3 border-t border-line p-4">
        <button className="btn btn-primary" disabled={count === 0 || pending} onClick={() => setConfirm(true)}>
          Apply {count > 0 ? `${count} change${count > 1 ? "s" : ""}` : "changes"}
        </button>
        <p className="min-w-0 flex-1 text-xs leading-relaxed text-dim">
          Tightening applies at once. Loosening is accepted only up to the ceiling set in the engine&apos;s env. Entry window{" "}
          {limits.effective.entry_start}–{limits.effective.no_entry_after} IST is set in the engine env.
        </p>
        <CommandStatus command={command} error={error} />
      </div>
      <ConfirmDialog
        open={confirm}
        title="Change hard limits"
        body={Object.entries(changes)
          .map(([k, v]) => `${k}: ${limits.effective[k as keyof LimitValues]} → ${v}`)
          .join(", ")}
        confirmLabel="Apply"
        onCancel={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          void send("update_limits" satisfies CommandType, changes);
          setEdits({});
        }}
      />
    </div>
  );
}
