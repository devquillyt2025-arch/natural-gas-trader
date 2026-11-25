// Pure data shaping for the price chart.
import { chartTime, IST_OFFSET_SEC, istDate } from "./time";
import type { Candle, Decision, TimeWindow } from "./types";

export const COLORS = {
  up: "#00b894",
  down: "#f5455c",
  accent: "#5b8cff",
  warn: "#f7b945",
  panel: "#0d1119",
  line: "rgba(255,255,255,0.09)",
  grid: "rgba(255,255,255,0.04)",
  dim: "#8d97ad",
  ema9: "#f2a83b",
  ema21: "#9b7bff",
  ema50: "#ec6aa8",
  shadeEia: "rgba(247,185,69,0.17)",
  shadeClosed: "rgba(163,173,194,0.07)",
};

export type Timeframe = "5m" | "15m" | "1d";
/** Width of an intraday bar in seconds. */
const width = (tf: Timeframe): number => (tf === "5m" ? 5 * 60 : 15 * 60);

/** The bar a decision belongs to. Decisions are made seconds after a bar closes, so step back a minute. */
export function decisionBarTime(ts: string, tf: Timeframe): number {
  if (tf === "1d") return Date.parse(`${istDate(ts)}T00:00:00Z`) / 1000;
  const seconds = chartTime(ts) - 60;
  return seconds - (seconds % width(tf));
}

export type MarkerKind = "long" | "short" | "exit" | "trail" | "rejected" | "hold";

export function markerKind(d: Decision): MarkerKind {
  if (d.action === "HOLD") return "hold";
  if (!d.approved) return "rejected";
  if (d.action === "ENTER_LONG") return "long";
  if (d.action === "ENTER_SHORT") return "short";
  if (d.action === "EXIT") return "exit";
  return "trail";
}

export interface Marker {
  id: string;
  time: number;
  position: "aboveBar" | "belowBar";
  shape: "arrowUp" | "arrowDown" | "circle" | "square";
  color: string;
  text?: string;
  size: number;
}

const STYLE: Record<MarkerKind, Omit<Marker, "id" | "time">> = {
  long: { position: "belowBar", shape: "arrowUp", color: COLORS.up, size: 1.6 },
  short: { position: "aboveBar", shape: "arrowDown", color: COLORS.down, size: 1.6 },
  exit: { position: "aboveBar", shape: "square", color: "#d7dde5", text: "✕", size: 0.8 },
  trail: { position: "belowBar", shape: "circle", color: COLORS.accent, size: 0.8 },
  rejected: { position: "aboveBar", shape: "circle", color: COLORS.warn, size: 0.7 },
  hold: { position: "belowBar", shape: "circle", color: "#5c6672", size: 0.3 },
};

/** Markers must be sorted by time. Only bars that are on the chart get markers. */
export function buildMarkers(decisions: Decision[], barTimes: Set<number>, tf: Timeframe, showHolds: boolean): Marker[] {
  const markers: Marker[] = [];
  for (const d of decisions) {
    const kind = markerKind(d);
    // on the daily chart, 50 dots a day would bury the entries and exits
    if (kind === "hold" && (!showHolds || tf === "1d")) continue;
    if (kind === "rejected" && tf === "1d") continue;
    const time = decisionBarTime(d.ts, tf);
    if (!barTimes.has(time)) continue;
    markers.push({ id: String(d.id), time, ...STYLE[kind] });
  }
  return markers.sort((a, b) => a.time - b.time);
}

export function decisionsAt(decisions: Decision[], time: number, tf: Timeframe): Decision[] {
  const found = decisions.filter((d) => decisionBarTime(d.ts, tf) === time);
  // entries, exits and rejections first; holds last
  return found.sort((a, b) => Number(a.action === "HOLD") - Number(b.action === "HOLD") || a.id - b.id);
}

/** Remaining bar slots of today's session, so the shading can show what is coming (e.g. the EIA window). */
export function futureSlots(last: Candle | undefined, now: number, marketClose: string | undefined): number[] {
  if (!last || !marketClose || !now) return [];
  const today = istDate(new Date(now).toISOString());
  if (istDate(last.ts) !== today) return [];
  const end = chartTime(`${today}T${marketClose}:00+05:30`);
  const slots: number[] = [];
  const step = width(last.tf);
  for (let t = chartTime(last.ts) + step; t < end; t += step) slots.push(t);
  return slots;
}

export interface Shade {
  time: number;
  value?: number;
  color?: string;
}

export function shading(barTimes: number[], windows: TimeWindow[], tf: Timeframe = "15m"): Shade[] {
  const spans = windows.map((w) => ({
    kind: w.kind,
    from: Date.parse(w.start) / 1000 + IST_OFFSET_SEC,
    to: Date.parse(w.end) / 1000 + IST_OFFSET_SEC,
  }));
  return barTimes.map((time) => {
    // a bar is shaded if any part of it falls inside the window
    const hit = spans.filter((s) => time < s.to && time + width(tf) > s.from);
    if (hit.some((s) => s.kind === "eia")) return { time, value: 1, color: COLORS.shadeEia };
    if (hit.length) return { time, value: 1, color: COLORS.shadeClosed };
    return { time };
  });
}
