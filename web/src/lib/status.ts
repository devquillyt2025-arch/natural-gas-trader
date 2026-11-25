// "What is it doing right now?" in one sentence, so a quiet screen is never mistaken for a stopped engine.
// Everything here is read from what the engine publishes; nothing is guessed about trading.
import { hhmm } from "./time";
import type { PositionState, SessionState, TimeWindow } from "./types";

export type ActivityKind = "offline" | "login" | "killed" | "closed" | "position" | "scanning" | "paused" | "waiting";

export interface Activity {
  kind: ActivityKind;
  title: string;
  detail: string;
  /** engine-clock ms of Claude's next look, when one is coming */
  nextLook: number | null;
}

/** Claude is asked just after a candle closes (the engine waits about 8 s). */
export function nextCandleClose(now: number, candleMin: number): number {
  const width = candleMin * 60_000;
  return (Math.floor((now - 8_000) / width) + 1) * width + 8_000;
}

/** When the block that covers `now` ends, following windows that overlap or touch. null if none covers now. */
export function blockEnds(windows: TimeWindow[], now: number): number | null {
  let end: number | null = null;
  const sorted = windows.map((w) => [Date.parse(w.start), Date.parse(w.end)] as const).sort((a, b) => a[0] - b[0]);
  for (const [from, to] of sorted) {
    if (from <= (end ?? now) && to > (end ?? now)) end = to;
  }
  return end;
}

export function activity(input: {
  engine: "ok" | "offline" | "checking" | null;
  authenticated: boolean;
  session: SessionState | null;
  position: PositionState | null;
  now: number;
}): Activity {
  const { engine, authenticated, session, position, now } = input;
  const quiet = (kind: ActivityKind, title: string, detail: string): Activity => ({ kind, title, detail, nextLook: null });

  if (engine === "offline") return quiet("offline", "Engine offline", "Nothing is being watched. Start it with npm run dev.");
  if (!session || engine === null || engine === "checking") return quiet("waiting", "Connecting", "Waiting for the engine's latest state.");
  if (!authenticated) return quiet("login", "Waiting for Upstox login", "No prices until you log in.");
  const look = nextCandleClose(now, session.candle_min ?? 15);
  if (position) {
    return {
      kind: "position",
      title: "In a trade",
      detail: session.market_open ? "Stop and target are checked every few seconds." : "Market closed.",
      nextLook: session.market_open && !session.kill ? look : null,
    };
  }
  if (session.kill) return quiet("killed", "Kill switch on", "No new trades until you press Resume.");
  if (!session.market_open) return quiet("closed", "Market closed", "New trades open again on the next trading day.");
  if (session.entries_allowed) {
    return { kind: "scanning", title: "Scanning for a setup", detail: "Most candles end in Hold.", nextLook: look };
  }

  const reason = session.block_reason ?? "new trades are blocked";
  const ends = blockEnds(session.windows, now);
  const closes = Date.parse(`${session.force_flat_at.slice(0, 10)}T${session.market_close}:00+05:30`);
  const when =
    ends === null ? "" : ends >= closes ? " No more new trades today." : ` Until ${hhmm(new Date(ends).toISOString())} IST.`;
  return quiet("paused", "Standing by", `${reason[0]!.toUpperCase()}${reason.slice(1)}.${when} Claude isn't asked while there is no position.`);
}
