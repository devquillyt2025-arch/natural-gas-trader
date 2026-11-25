"use client";

import { useEffect, useState } from "react";
import { ActionButton, CommandStatus, useCommand } from "@/components/commands";
import { Icon } from "@/components/icons";
import { LimitsEditor } from "@/components/limits-editor";
import { Chip, Empty, Panel, Ring, Row } from "@/components/ui";
import { getJson } from "@/lib/api";
import { useCockpit } from "@/lib/data";
import { computeStats, goLiveChecklist } from "@/lib/stats";
import { dayTime } from "@/lib/time";
import { useTrades } from "@/lib/trades";

/** Distinct IST days on which the engine recorded a paper decision. */
function usePaperDays(): number | null {
  const { decisions } = useCockpit();
  const [days, setDays] = useState<number | null>(null);
  useEffect(() => {
    getJson<{ days: number }>("/api/paper-days").then(
      (found) => setDays(found.days),
      () => setDays(null),
    );
  }, [decisions[0]?.ts.slice(0, 10)]);
  return days;
}

function Checklist() {
  const { state } = useCockpit();
  const { trades } = useTrades("paper");
  const paperDays = usePaperDays();
  const limits = state.limits?.effective;
  if (!trades || paperDays === null || !limits) return <Empty icon="check">Waiting for paper results</Empty>;
  const items = goLiveChecklist(computeStats(trades), paperDays, limits.daily_loss_cap_inr, limits.max_risk_inr);
  const passed = items.filter((i) => i.ok).length;
  return (
    <>
      <div className="flex items-center gap-3 border-b border-line px-4 py-3">
        <Ring share={passed / items.length} size={56} color={passed === items.length ? "#2ee6b6" : "#f7b945"}>
          {passed}/{items.length}
        </Ring>
        <p className="text-sm text-dim">{passed === items.length ? "Paper results pass these checks." : "Keep paper trading until all of these pass."}</p>
      </div>
      <ul data-testid="checklist">
        {items.map((item) => (
          <li key={item.label} className="flex items-start gap-3 border-b border-line px-4 py-2.5 last:border-0">
            <Chip tone={item.ok ? "up" : "warn"}>{item.ok ? "Pass" : "Not yet"}</Chip>
            <div className="min-w-0">
              <p className="text-sm">{item.label}</p>
              <p className="num break-words text-xs text-dim">{item.value}</p>
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

export default function ControlPage() {
  const { state, commands, ready } = useCockpit();
  const { send, command, error, pending } = useCommand();
  const { session, position, limits } = state;
  const run = (type: Parameters<typeof send>[0]) => void send(type);

  return (
    <div className="grid gap-3 lg:grid-cols-2 lg:items-start">
      <div className="min-w-0 space-y-3">
        <Panel title="Emergency controls" icon="power" testId="controls" className={session?.kill ? "border-down/60 shadow-glow-down" : "border-down/25"}>
          <div aria-hidden className="h-1 bg-[repeating-linear-gradient(135deg,rgba(245,69,92,0.55)_0_12px,transparent_12px_24px)]" />
          <div className="space-y-4 p-4">
            <div className="grid gap-2.5 sm:grid-cols-3">
              <ActionButton type="kill" label="Kill switch" className="btn btn-danger min-h-16" send={run} disabled={pending} />
              <ActionButton type="flatten" label="Flatten now" className="btn min-h-16 border-down/50 text-down-ink" send={run} disabled={pending || !position} />
              <ActionButton type="resume" label="Resume" className="btn btn-primary min-h-16" send={run} disabled={pending || (!session?.kill && !session?.safety_halt)} />
            </div>
            <CommandStatus command={command} error={error} />
            <div className="tile">
              <Row label="Kill switch" className={session?.kill ? "text-down-ink" : ""}>
                {session?.kill ? "ON: entries blocked" : "off"}
              </Row>
              <Row label="Safety halt">{session?.safety_halt ?? "none"}</Row>
              <Row label="Position">{position ? `${position.side} ${position.lots} lot` : "flat"}</Row>
            </div>
            <p className="text-xs leading-relaxed text-dim">
              Kill and Flatten close only the position this app opened. A position the app does not know about must be closed in the Upstox
              app. If the engine is offline, commands wait; Kill and Flatten still run when it returns.
            </p>
          </div>
        </Panel>

        <Panel title="Hard limits" icon="shield" testId="limits" delay={80}>
          <LimitsEditor limits={limits} />
        </Panel>
      </div>

      <div className="min-w-0 space-y-3">
        <Panel title="Mode (read only)" icon="lock" delay={40}>
          <div className="space-y-3 p-4 text-sm">
            <p>
              <Chip tone={session?.mode === "live" ? "solid-down" : "accent"}>{(session?.mode ?? "unknown").toUpperCase()}</Chip>
              <span className="ml-2 text-dim">{session?.model}</span>
            </p>
            <p className="leading-relaxed text-dim">
              This page cannot switch to live. Going live means setting <code className="num text-ink">MODE=live</code> and{" "}
              <code className="num text-ink">LIVE_CONFIRM=I_ACCEPT_THE_RISK</code> in <code className="num text-ink">engine/.env</code> and
              restarting the engine on its host.
            </p>
            <p className="flex gap-2 rounded-xl border border-warn/40 bg-warn/10 p-3 text-xs leading-relaxed text-warn">
              <Icon name="alert" size={16} />
              Before going live: Upstox currently rejects MCX orders sent through its API (error UDAPI1161) and requires a registered static
              IP for order APIs. Confirm both with Upstox first; until then only paper trading works.
            </p>
          </div>
        </Panel>

        <Panel title="Go-live checklist (paper results)" icon="trophy" delay={120}>
          <Checklist />
        </Panel>

        <Panel title="Command history" icon="scroll" testId="command-history" delay={160}>
          {!ready || commands.length === 0 ? (
            <Empty icon="scroll">No commands yet</Empty>
          ) : (
            <div className="max-h-96 overflow-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="th">Sent (IST)</th>
                    <th className="th">Command</th>
                    <th className="th">Status</th>
                    <th className="th w-full">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {commands.map((c) => (
                    <tr key={c.id} className="row-hover">
                      <td className="td num whitespace-nowrap">{dayTime(c.ts)}</td>
                      <td className="td whitespace-nowrap">{c.type}</td>
                      <td className="td">
                        <Chip tone={c.status === "acked" ? "up" : c.status === "failed" ? "down" : "warn"}>{c.status}</Chip>
                      </td>
                      <td className="td break-words text-xs text-dim">{c.result}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <form action="/auth/signout" method="post">
          <button className="btn w-full" type="submit">
            <Icon name="logout" /> Sign out
          </button>
        </form>
      </div>
    </div>
  );
}
