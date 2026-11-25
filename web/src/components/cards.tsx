"use client";

import { actionLabel, DASH, pnl as fmtPnl, price, rMultiple, rupees, toneClass } from "@/lib/format";
import { dayTime, duration, hhmm, weekdayDayMonth } from "@/lib/time";
import type { Decision, LimitsState, PositionState, SessionState } from "@/lib/types";
import { Icon, type IconName } from "./icons";
import { Chip, Empty, Panel, Ring, Row, Skeleton } from "./ui";

/** Stop ........ entry ........ target, with a marker where the price is. */
function Ladder({ position, ltp }: { position: PositionState; ltp: number }) {
  if (position.target === null) return null;
  const span = position.target - position.stop_loss;
  if (!span) return null;
  const at = (value: number) => Math.max(0, Math.min(100, ((value - position.stop_loss) / span) * 100));
  const entry = at(position.entry);
  const now = at(ltp);
  const winning = now >= entry;
  return (
    <div className="mt-3" aria-hidden>
      <div className="relative h-2 rounded-full bg-white/[0.07]">
        <span
          className={`absolute inset-y-0 rounded-full ${winning ? "bg-gradient-to-r from-up/30 to-up" : "bg-gradient-to-l from-down/30 to-down"}`}
          style={{ left: `${Math.min(entry, now)}%`, width: `${Math.abs(now - entry)}%`, transition: "all 600ms cubic-bezier(0.16,1,0.3,1)" }}
        />
        <span className="absolute top-1/2 h-3.5 w-px -translate-y-1/2 bg-accent-ink" style={{ left: `${entry}%` }} />
        <span
          className={`absolute top-1/2 h-4 w-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg ${winning ? "bg-up-ink shadow-glow-up" : "bg-down-ink shadow-glow-down"}`}
          style={{ left: `${now}%`, transition: "left 600ms cubic-bezier(0.16,1,0.3,1)" }}
        />
      </div>
      <div className="mt-1 flex justify-between text-xxs text-faint">
        <span>stop</span>
        <span>target</span>
      </div>
    </div>
  );
}

export function PositionCard({ position, ltp, now, ready = true }: { position: PositionState | null; ltp: number | null; now: number; ready?: boolean }) {
  if (!ready) {
    return (
      <Panel title="Position" icon="target">
        <Skeleton lines={5} />
      </Panel>
    );
  }
  if (!position) {
    return (
      <Panel title="Position" icon="target" testId="position-card" delay={40}>
        <div className="flex items-center gap-3 px-4 py-5">
          <span className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-dashed border-line-strong text-faint">
            <Icon name="swap" size={20} />
          </span>
          <div>
            <p className="num text-2xl font-semibold text-dim">Flat</p>
            <p className="text-xs text-faint">No open position. Nothing at risk.</p>
          </div>
        </div>
      </Panel>
    );
  }
  const direction = position.side === "LONG" ? 1 : -1;
  const unrealized = ltp === null ? null : (ltp - position.entry) * direction * position.qty;
  const risk = Math.abs(position.entry - position.initial_stop) * position.qty;
  const r = unrealized === null || risk === 0 ? null : unrealized / risk;
  const toStop = ltp === null ? null : (ltp - position.stop_loss) * direction;
  const toTarget = ltp === null || position.target === null ? null : (position.target - ltp) * direction;
  const distance = (points: number | null) => (points === null ? DASH : `${price(points)} · ${rupees(points * position.qty)}`);
  const glow = unrealized === null || unrealized === 0 ? "" : unrealized > 0 ? "border-up/40 shadow-glow-up" : "border-down/40 shadow-glow-down";

  return (
    <Panel
      title="Position"
      icon="target"
      testId="position-card"
      className={glow}
      delay={40}
      actions={
        <span className="flex flex-wrap gap-1.5">
          {position.flatten_reason && <Chip tone="warn">Closing</Chip>}
          {position.mode === "live" && <Chip tone={position.exchange_stop ? "up" : "solid-down"}>{position.exchange_stop ? "Stop at exchange" : "No exchange stop"}</Chip>}
          <Chip tone={direction === 1 ? "up" : "down"}>
            {position.side} {position.lots} lot
          </Chip>
        </span>
      }
    >
      <div className="px-4 pt-3">
        <div className="flex items-end justify-between gap-2">
          <span className={`num text-3xl font-semibold tracking-tight ${toneClass(unrealized)}`} data-testid="unrealized">
            {fmtPnl(unrealized)}
          </span>
          <span className={`num rounded-lg bg-white/[0.06] px-2 py-0.5 text-base ${toneClass(r)}`} data-testid="unrealized-r">
            {rMultiple(r)}
          </span>
        </div>
        <p className="text-xxs text-faint">Unrealised, before costs · {position.qty} mmBtu</p>
        {ltp !== null && <Ladder position={position} ltp={ltp} />}
      </div>
      <div className="px-4 py-2">
        <Row label="Entry">{price(position.entry)}</Row>
        <Row label="Stop" className="text-down-ink">
          {price(position.stop_loss)}
        </Row>
        <Row label="Target" className="text-up-ink">
          {price(position.target)}
        </Row>
        <Row label="Last">{price(ltp)}</Row>
        <Row label="To stop">{distance(toStop)}</Row>
        <Row label="To target">{distance(toTarget)}</Row>
        <Row label="In trade">{now ? duration(now - Date.parse(position.opened_at)) : DASH}</Row>
      </div>
    </Panel>
  );
}

export function RiskBudget({ limits, ready = true }: { limits: LimitsState | null; ready?: boolean }) {
  if (!ready || !limits) {
    return (
      <Panel title="Risk budget" icon="gauge" testId="risk-budget">
        {ready ? <Empty icon="gauge">No data from the engine yet</Empty> : <Skeleton lines={4} />}
      </Panel>
    );
  }
  const { usage, effective } = limits;
  const cap = effective.daily_loss_cap_inr;
  const used = Math.max(0, -usage.daily_pnl);
  const share = cap > 0 ? Math.min(1, used / cap) : 1;
  const level = usage.halted || share >= 1 ? "breached" : share >= 0.6 ? "high" : "ok";
  const fill = {
    ok: "bg-gradient-to-r from-accent to-cyan",
    high: "bg-gradient-to-r from-warn/70 to-warn",
    breached: "bg-gradient-to-r from-down-solid to-down",
  }[level];

  return (
    <Panel title="Risk budget" icon="gauge" testId="risk-budget" delay={80} actions={usage.halted ? <Chip tone="solid-down">Halted for today</Chip> : undefined}>
      <div className="px-4 py-3">
        <div className="flex items-baseline justify-between text-xs">
          <span className="text-dim">Daily loss used</span>
          <span className="num text-sm">
            <span className="font-semibold">{rupees(used)}</span> <span className="text-faint">of {rupees(cap)}</span>
          </span>
        </div>
        <div
          className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.07]"
          role="meter"
          aria-label="Daily loss used"
          aria-valuemin={0}
          aria-valuemax={cap}
          aria-valuenow={Math.round(used)}
          aria-valuetext={`${rupees(used)} of ${rupees(cap)}, ${level}`}
        >
          <div className={`h-full rounded-full transition-[width] duration-700 ease-out ${fill}`} style={{ width: `${share * 100}%` }} data-testid="loss-bar" data-level={level} />
        </div>
        <div className="mt-2">
          <Row label="P&L today" className={toneClass(usage.daily_pnl)}>
            {fmtPnl(usage.daily_pnl)}
          </Row>
          <div className="flex items-center justify-between py-1.5">
            <span className="text-xs text-dim">Trades today</span>
            <span className="flex items-center gap-2">
              <span className="flex gap-1" aria-hidden>
                {Array.from({ length: Math.min(effective.max_trades_per_day, 12) }, (_, i) => (
                  <span key={i} className={`h-2 w-4 rounded-full ${i < usage.trades_today ? "bg-accent-ink shadow-glow" : "bg-white/10"}`} />
                ))}
              </span>
              <span className="num text-sm" data-testid="trades-used">
                {usage.trades_today}/{effective.max_trades_per_day}
              </span>
            </span>
          </div>
          <Row label="Max risk per trade">{rupees(effective.max_risk_inr)}</Row>
          <Row label="Available for next trade">{rupees(usage.risk_budget)}</Row>
          <Row label="Max lots">{effective.max_lots}</Row>
        </div>
      </div>
    </Panel>
  );
}

function Countdown({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile min-w-0">
      <p className="text-xxs text-dim">{label}</p>
      <p className="num mt-0.5 truncate text-base font-semibold">{value}</p>
      {note && <p className="num truncate text-xxs text-faint">{note}</p>}
    </div>
  );
}

export function SessionCard({ session, now, ready = true }: { session: SessionState | null; now: number; ready?: boolean }) {
  if (!ready || !session) {
    return (
      <Panel title="Session" icon="clock">
        {ready ? <Empty icon="clock">No data from the engine yet</Empty> : <Skeleton lines={4} />}
      </Panel>
    );
  }
  const flatIn = Date.parse(session.force_flat_at) - now;
  const eiaIn = Date.parse(session.next_eia) - now;
  return (
    <Panel
      title="Session"
      icon="clock"
      testId="session-card"
      delay={120}
      actions={
        <Chip tone={session.market_open ? "up" : "neutral"} dot pulse={session.market_open}>
          {session.market_open ? "Open" : "Closed"}
        </Chip>
      }
    >
      <div className="px-4 py-3">
        <Row label="New entries" className={session.entries_allowed ? "text-up-ink" : "text-warn"}>
          {session.entries_allowed ? "Allowed" : "Blocked"}
        </Row>
        {session.block_reason && <p className="pb-1 text-right text-xs text-dim">{session.block_reason}</p>}
        <div className="mt-2 grid grid-cols-2 gap-2">
          <Countdown label="Force-flat in" value={now && session.market_open && flatIn > 0 ? duration(flatIn) : DASH} note={`market closes ${session.market_close}`} />
          <Countdown label="Next EIA storage" value={now && eiaIn > 0 ? `in ${duration(eiaIn)}` : "now"} note={`${weekdayDayMonth(session.next_eia)} ${hhmm(session.next_eia)}`} />
        </div>
        {!session.eia_verified && (
          <p className="mt-2 text-xs text-warn">
            Holiday shifts are not verified this far ahead. Check{" "}
            <a className="underline" href="https://ir.eia.gov/ngs/schedule.html" target="_blank" rel="noreferrer">
              the EIA schedule
            </a>
            .
          </p>
        )}
      </div>
    </Panel>
  );
}

export function Verdict({ decision, compact = false }: { decision: Decision; compact?: boolean }) {
  return (
    <span className={decision.approved ? "text-up-ink" : "text-warn"}>
      <span aria-hidden>{decision.approved ? "✓" : "✗"}</span>
      <span className="sr-only">{decision.approved ? "Approved: " : "Rejected: "}</span>{" "}
      <span className={compact ? "text-dim" : ""}>{decision.verdict}</span>
    </span>
  );
}

export function actionTone(action: string): "up" | "down" | "accent" | "neutral" {
  if (action === "ENTER_LONG") return "up";
  if (action === "ENTER_SHORT") return "down";
  if (action === "EXIT" || action === "TRAIL_STOP") return "accent";
  return "neutral";
}

const ACTION_ICON: Record<string, IconName> = { ENTER_LONG: "up", ENTER_SHORT: "down", EXIT: "flatten", TRAIL_STOP: "shield", HOLD: "pause" };
const BADGE = {
  up: "bg-up/15 text-up-ink",
  down: "bg-down/15 text-down-ink",
  accent: "bg-accent/15 text-accent-ink",
  neutral: "bg-white/[0.06] text-dim",
};

export function ActionBadge({ action, size = 28 }: { action: string; size?: number }) {
  return (
    <span className={`inline-flex shrink-0 items-center justify-center rounded-lg ${BADGE[actionTone(action)]}`} style={{ width: size, height: size }}>
      <Icon name={ACTION_ICON[action] ?? "pause"} size={Math.round(size * 0.55)} />
    </span>
  );
}

export function LatestDecision({ decision, ready = true }: { decision: Decision | undefined; ready?: boolean }) {
  return (
    <Panel title="Latest decision" icon="flame" testId="latest-decision" delay={160}>
      {!ready ? (
        <Skeleton lines={4} />
      ) : !decision ? (
        <Empty>No decisions yet</Empty>
      ) : (
        <div className="px-4 py-3">
          <div className="flex items-center gap-3">
            <ActionBadge action={decision.action} size={40} />
            <div className="min-w-0 flex-1">
              <Chip tone={actionTone(decision.action)}>{actionLabel(decision.action)}</Chip>
              <p className="num mt-1 text-xs text-dim">
                {dayTime(decision.ts)} · {price(decision.price)}
              </p>
            </div>
            {decision.confidence !== null && (
              <Ring label="confidence" share={decision.confidence} color={decision.confidence >= 0.6 ? "#5b8cff" : "#7f8aa2"}>
                {Math.round(decision.confidence * 100)}%
              </Ring>
            )}
          </div>
          <p className="mt-3 whitespace-pre-wrap break-words border-l-2 border-accent/60 pl-3 text-sm leading-relaxed">{decision.reasoning || DASH}</p>
          <p className="mt-3 text-xs">
            <Verdict decision={decision} />
          </p>
        </div>
      )}
    </Panel>
  );
}

export function DecisionFeed({
  decisions,
  onSelect,
  ready = true,
}: {
  decisions: Decision[];
  onSelect: (d: Decision) => void;
  ready?: boolean;
}) {
  return (
    <Panel title="Decision feed" icon="list" testId="decision-feed" delay={120}>
      {!ready ? (
        <Skeleton lines={6} />
      ) : decisions.length === 0 ? (
        <Empty>No decisions yet</Empty>
      ) : (
        <ul className="max-h-[28rem] overflow-y-auto">
          {decisions.slice(0, 20).map((d) => (
            <li key={d.id} className="rise border-b border-line last:border-0">
              <button
                className="row-hover grid w-full cursor-pointer grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-3 px-4 py-2.5 text-left md:grid-cols-[auto_5rem_6.5rem_5.5rem_6rem_minmax(0,1fr)_auto]"
                onClick={() => onSelect(d)}
              >
                <ActionBadge action={d.action} />
                <span className="text-sm font-medium">
                  {actionLabel(d.action)}
                  <span className="num block text-xxs font-normal text-faint md:hidden">{dayTime(d.ts)}</span>
                </span>
                <span className="num hidden text-xs text-dim md:block">{dayTime(d.ts)}</span>
                <span className="num text-right text-sm md:text-left">{price(d.price)}</span>
                <span className="hidden items-center gap-2 md:flex" title="confidence">
                  <span className="h-1 w-10 overflow-hidden rounded-full bg-white/10">
                    <span className="block h-full rounded-full bg-accent-ink" style={{ width: `${(d.confidence ?? 0) * 100}%` }} />
                  </span>
                  <span className="num text-xxs text-dim">{d.confidence === null ? "" : d.confidence.toFixed(2)}</span>
                </span>
                <span className="col-span-3 truncate text-xs md:col-span-1">
                  <Verdict decision={d} compact />
                </span>
                <Icon name="chevron" size={14} className="hidden text-faint md:block" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
