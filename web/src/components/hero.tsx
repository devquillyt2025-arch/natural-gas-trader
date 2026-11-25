"use client";

import { useRef } from "react";
import { DASH, price } from "@/lib/format";
import type { Activity, ActivityKind } from "@/lib/status";
import { duration, istDate } from "@/lib/time";
import type { Candle, SessionState } from "@/lib/types";
import { Icon, type IconName } from "./icons";

const LOOK: Record<ActivityKind, { icon: IconName; tone: string }> = {
  scanning: { icon: "radar", tone: "text-accent-ink bg-accent/15" },
  position: { icon: "target", tone: "text-up-ink bg-up/15" },
  paused: { icon: "pause", tone: "text-warn bg-warn/15" },
  closed: { icon: "clock", tone: "text-dim bg-white/5" },
  waiting: { icon: "clock", tone: "text-dim bg-white/5" },
  login: { icon: "lock", tone: "text-warn bg-warn/15" },
  killed: { icon: "power", tone: "text-down-ink bg-down/15" },
  offline: { icon: "alert", tone: "text-down-ink bg-down/15" },
};

/** Which way the last change went, kept so the price can flash once per tick. */
function useDirection(value: number | null): "up" | "down" | null {
  const memory = useRef<{ value: number | null; direction: "up" | "down" | null }>({ value: null, direction: null });
  if (value !== memory.current.value) {
    const before = memory.current.value;
    memory.current = { value, direction: before === null || value === null ? null : value > before ? "up" : "down" };
  }
  return memory.current.direction;
}

function Sparkline({ values, rising }: { values: number[]; rising: boolean }) {
  if (values.length < 2) return <div className="h-16" />;
  const low = Math.min(...values);
  const span = Math.max(...values) - low || 1;
  const points = values.map((v, i) => `${((i / (values.length - 1)) * 300).toFixed(1)},${(58 - ((v - low) / span) * 52).toFixed(1)}`);
  const color = rising ? "#2ee6b6" : "#ff7586";
  return (
    <svg viewBox="0 0 300 64" preserveAspectRatio="none" className="h-16 w-full" role="img" aria-label={`Recent prices, ${rising ? "rising" : "falling"}`}>
      <defs>
        <linearGradient id="spark" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.35" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`M0,64 L${points.join(" L")} L300,64 Z`} fill="url(#spark)" />
      <polyline points={points.join(" ")} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

export function Hero({
  price: last,
  session,
  intraday,
  forming,
  daily,
  now,
  status,
}: {
  price: number | null;
  session: SessionState | null;
  intraday: Candle[];
  forming: Candle | null;
  daily: Candle[];
  now: number;
  status: Activity;
}) {
  const direction = useDirection(last);
  const today = now ? istDate(new Date(now).toISOString()) : "";
  const previous = today ? [...daily].reverse().find((c) => istDate(c.ts) < today) : undefined;
  const change = last !== null && previous ? last - previous.c : null;
  const percent = change !== null && previous ? (change / previous.c) * 100 : null;
  const bars = forming && forming.ts > (intraday.at(-1)?.ts ?? "") ? [...intraday, forming] : intraday;
  const todays = bars.filter((c) => istDate(c.ts) === today);
  const low = todays.length ? Math.min(...todays.map((c) => c.l), last ?? Infinity) : null;
  const high = todays.length ? Math.max(...todays.map((c) => c.h), last ?? -Infinity) : null;
  const at = low !== null && high !== null && last !== null && high > low ? ((last - low) / (high - low)) * 100 : 50;
  const spark = bars.slice(-70).map((c) => c.c);
  const rising = spark.length > 1 && spark.at(-1)! >= spark[0]!;
  const look = LOOK[status.kind];
  const wait = status.nextLook !== null && now ? status.nextLook - now : null;

  return (
    <section className="glass rise relative overflow-hidden" aria-label="Market now" data-testid="hero">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent to-transparent" />
      <div className="grid gap-4 p-4 md:grid-cols-[auto_minmax(0,1fr)_minmax(15rem,20rem)] md:items-center md:gap-8 md:px-6 md:py-5">
        <div className="min-w-0">
          <p className="label">
            {session?.trading_symbol ?? session?.symbol ?? "Natural gas"} <span className="text-faint">· ₹ per mmBtu</span>
          </p>
          <p key={last ?? "none"} className={`num mt-1 text-4xl font-semibold tracking-tight md:text-5xl ${direction ? `flash-${direction}` : ""}`} data-testid="hero-price">
            {price(last)}
          </p>
          <p className="num mt-1 flex items-center gap-2 text-sm">
            {change === null ? (
              <span className="text-faint">{DASH}</span>
            ) : (
              <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 ${change >= 0 ? "bg-up/15 text-up-ink" : "bg-down/15 text-down-ink"}`}>
                <Icon name={change >= 0 ? "up" : "down"} size={13} />
                {change >= 0 ? "+" : "−"}
                {Math.abs(change).toFixed(2)} ({Math.abs(percent!).toFixed(2)}%)
              </span>
            )}
            <span className="text-xs text-faint">vs previous close</span>
          </p>
        </div>

        <div className="min-w-0">
          <Sparkline values={spark} rising={rising} />
          <div className="mt-2">
            <div className="relative h-1.5 rounded-full bg-gradient-to-r from-down/50 via-white/10 to-up/50">
              {low !== null && (
                <span
                  className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg bg-white shadow-glow transition-[left] duration-500 ease-out"
                  style={{ left: `${at}%` }}
                />
              )}
            </div>
            <div className="num mt-1 flex justify-between text-xxs text-dim">
              <span>Low {price(low)}</span>
              <span className="text-faint">today&apos;s range</span>
              <span>High {price(high)}</span>
            </div>
          </div>
        </div>

        <div className="tile flex items-start gap-3" role="status" data-testid="activity" data-kind={status.kind}>
          <span className={`relative inline-flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full ${look.tone}`}>
            {status.kind === "scanning" && <span className="radar absolute inset-0 opacity-40" aria-hidden />}
            <Icon name={look.icon} size={18} className="relative" />
          </span>
          <div className="min-w-0">
            <p className="text-sm font-semibold">{status.title}</p>
            <p className="text-xs leading-relaxed text-dim">{status.detail}</p>
            {wait !== null && wait > 0 && (
              <p className="num mt-1 text-xs text-accent-ink">
                Claude looks again in <span className="font-semibold">{duration(wait)}</span>
              </p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
