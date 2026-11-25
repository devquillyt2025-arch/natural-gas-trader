import { istDate } from "./time";
import type { Trade } from "./types";

export type ExitCategory = "stop" | "target" | "agent" | "forced" | "other";

export function exitCategory(reason: string): ExitCategory {
  if (reason === "stop") return "stop";
  if (reason === "target") return "target";
  if (reason === "agent") return "agent";
  if (reason.startsWith("forced")) return "forced";
  return "other";
}

export interface Stats {
  count: number;
  wins: number;
  losses: number;
  winRate: number | null;
  netPnl: number;
  avgWin: number | null;
  avgLoss: number | null;
  expectancyR: number | null;
  /** null when there are no trades or no losses yet */
  profitFactor: number | null;
  maxDrawdown: number;
  bestDay: { date: string; pnl: number } | null;
  worstDay: { date: string; pnl: number } | null;
  tradingDays: number;
  exits: Record<ExitCategory, number>;
}

const byClose = (a: Trade, b: Trade) => Date.parse(a.closed_at) - Date.parse(b.closed_at);
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);

export function dailyPnl(trades: Trade[]): Map<string, number> {
  const days = new Map<string, number>();
  for (const t of [...trades].sort(byClose)) {
    const day = istDate(t.closed_at);
    days.set(day, (days.get(day) ?? 0) + t.pnl);
  }
  return days;
}

export function equityCurve(trades: Trade[]): { ts: string; equity: number }[] {
  let equity = 0;
  return [...trades].sort(byClose).map((t) => {
    equity += t.pnl;
    return { ts: t.closed_at, equity };
  });
}

/** Largest peak-to-trough fall of cumulative P&L, starting from zero. Returned as a positive number. */
export function maxDrawdown(trades: Trade[]): number {
  let peak = 0;
  let worst = 0;
  for (const point of equityCurve(trades)) {
    peak = Math.max(peak, point.equity);
    worst = Math.max(worst, peak - point.equity);
  }
  return worst;
}

export function computeStats(trades: Trade[]): Stats {
  const wins = trades.filter((t) => t.pnl > 0);
  const losses = trades.filter((t) => t.pnl < 0);
  const grossWin = wins.reduce((a, t) => a + t.pnl, 0);
  const grossLoss = -losses.reduce((a, t) => a + t.pnl, 0);
  const days = [...dailyPnl(trades)].map(([date, value]) => ({ date, pnl: value }));
  const ranked = [...days].sort((a, b) => a.pnl - b.pnl);
  const exits: Record<ExitCategory, number> = { stop: 0, target: 0, agent: 0, forced: 0, other: 0 };
  for (const t of trades) exits[exitCategory(t.exit_reason)] += 1;
  return {
    count: trades.length,
    wins: wins.length,
    losses: losses.length,
    winRate: trades.length ? wins.length / trades.length : null,
    netPnl: trades.reduce((a, t) => a + t.pnl, 0),
    avgWin: mean(wins.map((t) => t.pnl)),
    avgLoss: mean(losses.map((t) => t.pnl)),
    expectancyR: mean(trades.flatMap((t) => (t.r_multiple === null ? [] : [t.r_multiple]))),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : null,
    maxDrawdown: maxDrawdown(trades),
    bestDay: ranked.at(-1) ?? null,
    worstDay: ranked[0] ?? null,
    tradingDays: days.length,
    exits,
  };
}

export const GO_LIVE_MIN_DAYS = 15;
/** Max drawdown allowed, as a multiple of the daily loss cap. */
export const GO_LIVE_DRAWDOWN_CAPS = 3;

export interface ChecklistItem {
  label: string;
  value: string;
  ok: boolean;
}

export function goLiveChecklist(stats: Stats, paperDays: number, dailyCap: number, riskPerTrade: number): ChecklistItem[] {
  const rupee = (x: number) => `₹${Math.round(Math.abs(x)).toLocaleString("en-IN")}`;
  const drawdownCap = GO_LIVE_DRAWDOWN_CAPS * dailyCap;
  // one stopped trade plus costs may overshoot the cap before it can bind
  const worstAllowed = dailyCap + riskPerTrade;
  return [
    { label: `At least ${GO_LIVE_MIN_DAYS} paper trading days`, value: `${paperDays}`, ok: paperDays >= GO_LIVE_MIN_DAYS },
    {
      label: "Positive expectancy",
      value:
        stats.expectancyR === null
          ? "no trades"
          : `${stats.expectancyR.toFixed(2)}R · win ${Math.round((stats.winRate ?? 0) * 100)}% · avg win ${rupee(stats.avgWin ?? 0)} / loss ${rupee(stats.avgLoss ?? 0)}`,
      ok: stats.expectancyR !== null && stats.expectancyR > 0,
    },
    {
      label: `Max drawdown within ${rupee(drawdownCap)} (${GO_LIVE_DRAWDOWN_CAPS}× daily cap)`,
      value: rupee(stats.maxDrawdown),
      ok: stats.count > 0 && stats.maxDrawdown <= drawdownCap,
    },
    {
      label: `No day worse than ${rupee(worstAllowed)} (cap + one trade)`,
      value: stats.worstDay ? `${stats.worstDay.pnl < 0 ? "−" : "+"}${rupee(stats.worstDay.pnl)}` : "no trades",
      ok: stats.worstDay !== null && stats.worstDay.pnl >= -worstAllowed,
    },
  ];
}
